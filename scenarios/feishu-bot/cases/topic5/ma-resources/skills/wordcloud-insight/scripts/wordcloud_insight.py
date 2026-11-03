"""Local deterministic helpers; the calling Agent writes the interpretation."""
from __future__ import annotations
import argparse
import base64
import csv
from contextlib import redirect_stdout
import hashlib
import html
import importlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import sys

VERSION = '0.2.6'
DISPLAY_LIMIT = 100
METRICS = {'post_count': '出现帖子数', 'occurrence_count': '出现次数', 'unknown': '数值（口径未确认）'}
PALETTE = ['#65adf3', '#b196f2', '#76d887', '#f1c55f', '#e68862', '#7e89e8']

class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

def require(ok, code, message):
    if not ok:
        raise Problem(code, message)

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (ValueError, OSError) as e:
        raise Problem('READ_JSON', str(e)) from e

def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()

def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def text_field(value, name, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())), 'INVALID_TEXT', f'{name} must be text')
    require(not any(ord(c) < 32 and c not in '\n\t\r' for c in value), 'INVALID_TEXT', f'Control character in {name}')
    return value

def load_input(path):
    data = path if isinstance(path, dict) else read_json(path)
    require(isinstance(data, dict) and data.get('schema_version') == 1, 'INPUT_SCHEMA', 'schema_version must be 1')
    ctx = data.get('context')
    require(isinstance(ctx, dict), 'INPUT_CONTEXT', 'context is required')
    for key in ('subject', 'period', 'platform', 'sentiment', 'content_type'):
        text_field(ctx.get(key), 'context.' + key)
    require(ctx['sentiment'] in ('全部', '正面', '负面', '中性', '未知'), 'INPUT_SENTIMENT', 'Use the existing sentiment filter; do not infer one')
    require(ctx['content_type'] in ('全部', 'PGC', 'UGC', '未知'), 'INPUT_CONTENT_TYPE', 'content_type must be 全部/PGC/UGC/未知')
    for key in ('project', 'objective', 'background'):
        if key in ctx:
            text_field(ctx[key], 'context.' + key, empty=True)
    require(data.get('metric') in METRICS, 'INPUT_METRIC', 'metric must be post_count/occurrence_count/unknown')
    words = data.get('words')
    require(isinstance(words, list) and 1 <= len(words) <= 500, 'INPUT_WORDS', 'Supply 1–500 words; never silently truncate')
    seen = set()
    for row in words:
        require(isinstance(row, dict), 'INPUT_WORDS', 'Each word must be an object')
        term = text_field(row.get('term'), 'term')
        require(len(term) <= 200 and not any(ord(c) < 32 for c in term), 'INVALID_TERM', 'Term must be one line, at most 200 characters')
        require(term not in seen, 'DUPLICATE_TERM', f'Duplicate original term: {term}')
        seen.add(term)
        value = row.get('value')
        require(isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 10**15 and math.isfinite(value), 'INVALID_VALUE', f'Invalid frequency for {term}')
        require(value > 0, 'ZERO_FREQUENCY', f'Zero frequency for {term}; obtain an explicit handling decision')
    return data

def font_candidates():
    win = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    noto_bold = [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
             Path('/usr/share/fonts/truetype/noto/NotoSansSC-Bold.ttf'),
             Path.home()/'.fonts/NotoSansSC-Bold.otf', Path('/Library/Fonts/NotoSansCJK-Bold.ttc'),
             Path(__file__).resolve().parents[1]/'assets/fonts/NotoSansCJK-Bold.ttc']
    # Customer order: YaHei Bold on Windows; Noto CJK Bold elsewhere.
    # Regular fonts are considered only after all preferred bold candidates.
    if sys.platform == 'win32':
        paths = [win/'msyhbd.ttc', *noto_bold, win/'msyh.ttc',
                 Path.home()/'.fonts/NotoSansSC-Regular.otf']
    else:
        paths = [*noto_bold, win/'msyhbd.ttc', win/'msyh.ttc',
                 Path.home()/'.fonts/NotoSansSC-Regular.otf']
    return [p for p in paths if p.is_file()]

def packages():
    found, missing = {}, []
    for module, distribution in [('numpy', 'numpy'), ('PIL', 'Pillow'), ('wordcloud', 'wordcloud'), ('fontTools', 'fonttools'), ('openpyxl', 'openpyxl')]:
        try:
            importlib.import_module(module)
            found[distribution] = importlib.metadata.version(distribution)
        except (ImportError, OSError, importlib.metadata.PackageNotFoundError) as e:
            missing.append({'package': distribution, 'error': str(e)})
    return found, missing

def prepare(args):
    context = read_json(args.context)
    require(isinstance(context, dict) and 'words' not in context, 'INPUT_CONTEXT', 'context JSON should contain schema_version/context/metric, without words')
    src = Path(args.table)
    if src.suffix.lower() == '.csv':
        with src.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
    elif src.suffix.lower() == '.xlsx':
        try:
            import openpyxl
        except ImportError as e:
            raise Problem('DEPENDENCY_MISSING', 'Install openpyxl in the task environment') from e
        book = openpyxl.load_workbook(src, read_only=True, data_only=True)
        try:
            require(args.sheet or len(book.sheetnames) == 1, 'AMBIGUOUS_SHEET', 'Multiple sheets: specify --sheet')
            name = args.sheet or book.sheetnames[0]
            require(name in book.sheetnames, 'INVALID_SHEET', f'Unknown sheet: {name}')
            rows = list(book[name].values)
        finally:
            book.close()
    else:
        raise Problem('TABLE_FORMAT', 'Use UTF-8 CSV or XLSX')
    require(len(rows) >= 2, 'EMPTY_TABLE', 'Table must contain a header and data')
    headers = [str(v).strip() if v is not None else '' for v in rows[0]]
    def column(explicit, choices):
        matches = [i for i, h in enumerate(headers) if h == explicit] if explicit else [i for i, h in enumerate(headers) if h in choices]
        require(len(matches) == 1, 'AMBIGUOUS_COLUMN', 'Specify --term-column/--value-column to select unique columns')
        return matches[0]
    ti = column(args.term_column, ['关键词', '词项', '词', 'term', 'word'])
    vi = column(args.value_column, ['频次', '词频', '声量', 'value', 'count', 'frequency'])
    words = []
    for n, row in enumerate(rows[1:], 2):
        if all(v is None or v == '' for v in row):
            continue
        require(len(row) > max(ti, vi), 'TABLE_ROW', f'Missing column in row {n}')
        term, value = row[ti], row[vi]
        require(isinstance(term, str) and not isinstance(value, bool), 'TABLE_ROW', f'Term must be text and frequency numeric: row {n}')
        try:
            if isinstance(value, str):
                value = float(value)
                if math.isfinite(value) and value.is_integer():
                    value = int(value)
        except ValueError as e:
            raise Problem('TABLE_VALUE', f'Invalid number in row {n}') from e
        words.append({'term': term, 'value': value})
    payload = dict(context, words=words, provenance={'source_name':src.name, 'source_sha256':file_hash(src)})
    dest = Path(args.output)
    require(not dest.exists(), 'OUTPUT_EXISTS', str(dest))
    # Validate before writing, using the same parser without temporary source files.
    validate_object(payload)
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_json(dest, payload)
    return {'status': 'PREPARED', 'words': len(words), 'source_sha256': file_hash(src), 'output': str(dest)}

def validate_object(data):
    # Keep normalization and CLI validation on the same code path.
    return load_input(data)

def glyph(term, size, font_path):
    from PIL import Image, ImageFont, ImageDraw
    font = ImageFont.truetype(str(font_path), size)
    box = font.getbbox(term)
    require(box[2] > box[0] and box[3] > box[1], 'INVALID_GLYPH', f'Empty glyph: {term}')
    g = Image.new('L', (box[2]-box[0], box[3]-box[1]))
    ImageDraw.Draw(g).text((-box[0], -box[1]), term, font=font, fill=255)
    return g

def select_font(requested, terms):
    from fontTools.ttLib import TTFont
    candidates = [Path(requested)] if requested else font_candidates()
    require(candidates, 'FONT_MISSING', 'Supply --font with a CJK font path')
    required = {ord(c) for t in terms for c in t if not c.isspace()}
    diagnostics = []
    for path in candidates:
        require(path.is_file(), 'FONT_MISSING', str(path))
        try:
            font = TTFont(str(path), fontNumber=0)
            cmap = font.getBestCmap() or {}
            missing = sorted(required-set(cmap))
            font.close()
            if not missing:
                return path
            diagnostics.append({'font': path.name, 'missing_characters': ''.join(chr(c) for c in missing[:20])})
        except Exception as e:
            diagnostics.append({'font': path.name, 'error': str(e)})
    raise Problem('UNSUPPORTED_GLYPH', json.dumps(diagnostics, ensure_ascii=False))

def blend(color, strength):
    bg = '#2c354a'
    return '#'+''.join(f'{round(int(color[i:i+2],16)*strength + int(bg[i:i+2],16)*(1-strength)):02x}' for i in (1,3,5))

def place(words, sizes, font, width, height):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    mask = Image.new('L', (width, height))
    ImageDraw.Draw(mask).ellipse((round(width*20/1400), round(height*18/880), round(width*1380/1400), round(height*862/880)), fill=255)
    allowed = np.array(mask)>0
    occupied = (~allowed).astype(np.uint8)
    layout = []
    top = words[0]['value']
    for rank, row in enumerate(words):
        size = sizes[row['term']]
        g = glyph(row['term'], size, font)
        gw, gh = g.size
        pw, ph = gw+4, gh+4
        if pw >= width-16 or ph >= height-16:
            return None
        ii = np.pad(occupied.astype(np.int32).cumsum(0).cumsum(1), ((1,0),(1,0)))
        yy, xx = np.meshgrid(np.arange(8,height-ph-8,2), np.arange(8,width-pw-8,2), indexing='ij')
        valid = (ii[yy+ph,xx+pw]-ii[yy,xx+pw]-ii[yy+ph,xx]+ii[yy,xx])==0
        dx = [0,-35,42,-20,25,0,15,-25,20,0][rank%10]*width/1400 if rank<10 else 0
        dy = [0,12,-10,18,-14,0,-8,8,0,0][rank%10]*height/880 if rank<10 else 0
        score = ((xx+pw/2-width/2-dx)/(width/2))**2+((yy+ph/2-height/2-dy)/(height/2))**2
        score = np.where(valid, score, np.inf)
        if not np.isfinite(score).any():
            return None
        yi, xi = np.unravel_index(score.argmin(), score.shape)
        x, y = int(xx[yi,xi]), int(yy[yi,xi])
        padded = Image.new('L',(pw,ph)); padded.paste(g,(2,2))
        occupied[y:y+ph,x:x+pw] |= (np.array(padded.filter(ImageFilter.MaxFilter(5)))>0).astype(np.uint8)
        relative = row['value']/top
        strength = 1 if relative >= 28/87 else (.78 if relative >= 18/87 else .53)
        layout.append(dict(row, size=size, x=x+2, y=y+2, width=gw, height=gh, color=blend(PALETTE[rank%6],strength)))
    return layout, allowed

def display_selection(data):
    # Stable sorting preserves source order for equal frequencies, including cutoff ties.
    ordered = sorted(data['words'], key=lambda row: -row['value'])
    shown = ordered[:DISPLAY_LIMIT]
    record = dict(policy='top100_by_frequency', limit=DISPLAY_LIMIT,
                  tie_break='source_order', input_count=len(ordered),
                  displayed_count=len(shown), omitted_words=ordered[DISPLAY_LIMIT:])
    return shown, record


def validate_display_selection(meta, data):
    shown, selection = display_selection(data)
    layout = meta.get('layout')
    require(isinstance(layout, list) and all(isinstance(row, dict) for row in layout),
            'DATA_MISMATCH', 'Invalid rendered layout')
    require(meta.get('selection') == selection and
            [(row.get('term'), row.get('value')) for row in layout] ==
            [(row['term'], row['value']) for row in shown] and
            meta.get('all_display_words_preserved') is True and
            meta.get('all_words_preserved') is (len(shown) == len(data['words'])),
            'DATA_MISMATCH', 'Rendered selection differs from Top100 policy; re-render in a new directory')
    return selection


def render(args):
    data = load_input(args.input)
    found, missing = packages()
    require(not missing, 'DEPENDENCY_MISSING', json.dumps(missing))
    import numpy as np
    from PIL import Image
    from wordcloud import WordCloud
    width, height = args.width, args.height
    require(320 <= width <= 2400 and 240 <= height <= 1600, 'CANVAS_SIZE', 'width 320–2400; height 240–1600')
    require(6 <= args.min_font_size <= args.max_font_size <= 512, 'FONT_SIZE', 'Use 6 <= min <= max <= 512')
    dest = Path(args.out)
    require(not dest.exists(), 'OUTPUT_EXISTS', 'Use a new output directory')
    words, selection = display_selection(data)
    font = select_font(args.font, [t['term'] for t in words])
    y, x = np.ogrid[:height,:width]
    size_mask = np.where(((x-width/2)/(width*.475))**2+((y-height/2)/(height*.46))**2>1,255,0).astype('uint8')
    frequencies = {t['term']: t['value'] for t in words}
    result, attempts = None, []
    maximum = args.max_font_size
    for attempt in range(8):
        cloud = WordCloud(font_path=str(font), width=width, height=height, mask=size_mask, max_words=len(words), max_font_size=maximum, min_font_size=1, prefer_horizontal=1.0, relative_scaling='auto', repeat=False, margin=5, random_state=37, collocations=False).generate_from_frequencies(frequencies)
        sizes = {item[0][0]: max(args.min_font_size,int(item[1])) for item in cloud.layout_}
        attempts.append({'maximum_font': maximum, 'size_mapping_count': len(sizes)})
        if len(sizes)==len(words):
            result = place(words, sizes, font, width, height)
        if result:
            break
        next_maximum = max(args.min_font_size, int(maximum*.86))
        if next_maximum == maximum:
            break
        maximum = next_maximum
    require(result is not None, 'LAYOUT_CAPACITY', 'Selected display words cannot fit at these constraints. Increase canvas; no additional words were dropped.')
    layout, allowed = result
    image = Image.new('RGBA',(width,height),(0,0,0,0))
    occupied = np.zeros((height,width),dtype=np.uint16)
    for item in layout:
        g = glyph(item['term'],item['size'],font)
        gx, gy = item['x'], item['y']; gw, gh = g.size
        ink = np.array(g)>0
        require(gx>=0 and gy>=0 and gx+gw<=width and gy+gh<=height, 'CLIPPED_GLYPH', item['term'])
        require(not (ink & ~allowed[gy:gy+gh,gx:gx+gw]).any(), 'MASK_OVERFLOW', item['term'])
        occupied[gy:gy+gh,gx:gx+gw] += ink
        layer = Image.new('RGBA',g.size,item['color']); layer.putalpha(g)
        image.alpha_composite(layer,(gx,gy))
    require(not (occupied>1).any(), 'GLYPH_OVERLAP', 'Unexpected glyph collision')
    require({t['term']:t['value'] for t in layout}==frequencies, 'DATA_MISMATCH', 'Rendered words differ from input')
    dest.mkdir(parents=True)
    image.save(dest/'wordcloud.png')
    # Flatten the same raster over white; never run layout a second time.
    white = white_variant(image)
    white.save(dest/'wordcloud-white.png')
    metadata = dict(skill_version=VERSION, input_sha256=digest(data), png_sha256=file_hash(dest/'wordcloud.png'), font_name=font.name, font_sha256=file_hash(font), packages=found, canvas=[width,height], style='natural-fill-v0.3-transparent', attempts=attempts, seed=37, metric=data['metric'], all_words_preserved=len(words)==len(data['words']), all_display_words_preserved=True, selection=selection, glyph_overlap_pixels=0, outside_mask_pixels=0, rgba=True, layout=layout)
    metadata.update(white_png_sha256=file_hash(dest/'wordcloud-white.png'), white_background='#FFFFFF', white_mode='RGB')
    write_json(dest/'render.json', metadata)
    return {'status': 'RENDERED', 'count': len(layout), 'input_count':len(data['words']), 'output': str(dest), 'attempts':len(attempts), 'images':['wordcloud.png','wordcloud-white.png']}


def white_variant(transparent):
    from PIL import Image
    return Image.alpha_composite(Image.new('RGBA', transparent.size, 'white'), transparent).convert('RGB')


def validate_white_variant(dest, meta):
    from PIL import Image, ImageChops
    path = dest/'wordcloud-white.png'
    require(path.is_file() and meta.get('white_png_sha256'), 'IMAGE_MISMATCH', 'White PNG or its render hash is missing; rerun render in a new directory')
    require(meta['white_png_sha256']==file_hash(path), 'IMAGE_MISMATCH', 'White PNG changed since render')
    with Image.open(dest/'wordcloud.png') as transparent, Image.open(path) as white:
        require(transparent.format=='PNG' and white.format=='PNG' and transparent.mode=='RGBA' and white.mode=='RGB' and 'transparency' not in white.info and transparent.size==white.size, 'IMAGE_MISMATCH', 'Expected same-size RGBA transparent and fully opaque RGB white PNGs')
        require(ImageChops.difference(white, white_variant(transparent)).getbbox() is None, 'IMAGE_MISMATCH', 'White PNG must exactly match the transparent PNG composited over white')

def markdown(value):
    # Source text is plain text, never executable HTML/Markdown links.
    value = value.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    return re.sub(r'([\\`*_{}\[\]()#+.!|~-])',r'\\\1',value).replace('\r',' ').replace('\n',' ')

def format_value(value):
    return str(int(value)) if float(value).is_integer() else str(value)

def compose(args):
    data = load_input(args.input); topics = read_json(args.topics); dest = Path(args.out)
    meta = read_json(dest/'render.json')
    require(meta.get('input_sha256')==digest(data), 'INPUT_MISMATCH', 'Analysis input differs from rendered input')
    selection = validate_display_selection(meta, data)
    require(meta.get('png_sha256')==file_hash(dest/'wordcloud.png'), 'IMAGE_MISMATCH', 'PNG changed since render')
    validate_white_variant(dest, meta)
    require(isinstance(topics,dict) and isinstance(topics.get('topics'),list) and len(topics['topics'])<=5, 'TOPIC_SCHEMA', 'topics must contain 0–5 topics')
    limits = topics.get('limitations',[])
    require(isinstance(limits,list), 'TOPIC_SCHEMA', 'limitations must be a list')
    for s in limits:
        text_field(s,'limitation')
    require(topics['topics'] or limits, 'EMPTY_ANALYSIS', 'No topics requires a specific limitation')
    summary = text_field(topics.get('summary',''),'summary',empty=True)
    values = {t['term']:t['value'] for t in data['words']}
    normalized = []
    for topic in topics['topics']:
        require(isinstance(topic,dict), 'TOPIC_SCHEMA', 'Each topic must be an object')
        for key in ('title','interpretation','rationale'):
            text_field(topic.get(key),key)
        refs = topic.get('terms')
        require(isinstance(refs,list) and refs and all(isinstance(t,str) for t in refs), 'TOPIC_TERMS', 'Use original term strings')
        require(len(set(refs))==len(refs) and all(t in values for t in refs), 'UNKNOWN_TERM', 'Topic references contain duplicates or terms absent from input')
        normalized.append(dict(topic,terms=sorted(refs,key=lambda t:-values[t])))
    post_review = None
    if getattr(args, 'post_review', None):
        from post_evidence import load_post_evidence
        try:
            post_review = load_post_evidence(args.post_review, digest(data), normalized)
        except (ValueError, OSError, TypeError, KeyError) as e:
            raise Problem('POST_EVIDENCE', str(e)) from e
    else:
        for t in normalized:
            refs = t.get('post_ids', [])
            require(isinstance(refs, list) and not refs, 'POST_EVIDENCE_REQUIRED', 'Post citations require --post-review')
    ctx=data['context']
    scope='｜'.join(ctx[k] for k in ('period','platform','sentiment','content_type'))+'｜'+METRICS[data['metric']]
    md=[f'# {markdown(ctx["subject"])}：词云议题分析', '', markdown(scope), '']
    sections=[]
    if summary:
        md.extend([markdown(summary),''])
        sections.append('<p>'+html.escape(summary)+'</p>')
    for n,t in enumerate(normalized,1):
        title=f'议题{n}：{t["title"]}'
        refs='、'.join(f'{word}（{format_value(values[word])}）' for word in t['terms'])
        md.extend([f'## {markdown(title)}','',f'代表词：{markdown(refs)}','',markdown(t['interpretation']),''])
        sections.append('<article><h2>'+html.escape(title)+'</h2><p class="terms">代表词：'+html.escape(refs)+'</p><p>'+html.escape(t['interpretation'])+'</p></article>')
    if limits:
        md.extend(['分析说明：'+markdown('；'.join(limits)),''])
        sections.append('<p class="note">分析说明：'+html.escape('；'.join(limits))+'</p>')
    png='data:image/png;base64,'+base64.b64encode((dest/'wordcloud.png').read_bytes()).decode()
    white_png='data:image/png;base64,'+base64.b64encode((dest/'wordcloud-white.png').read_bytes()).decode()
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>词云议题分析</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#263448;font:15px/1.7 "Microsoft YaHei",sans-serif}main{max-width:1300px;margin:auto;padding:24px}h1{font-size:26px;margin:0}h2{font-size:18px;margin:0 0 8px}.scope,.terms,.note{color:#66768a;font-size:13px}.grid{display:grid;grid-template-columns:1.2fr 1fr;gap:24px;align-items:start}.stage{background:#2c354a;border-radius:10px;overflow:hidden}.stage img{width:100%;display:block}article{background:white;border:1px solid #e0e5ec;border-radius:9px;padding:18px;margin-bottom:14px}article p{margin:8px 0}nav{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}button,a{font:inherit;font-size:13px;border:1px solid #cad5e3;border-radius:5px;padding:6px 10px;background:white;color:#355f90;cursor:pointer}button[aria-pressed=true]{background:#365b88;color:white}.checker{background-image:conic-gradient(#e6e9ed 25%,white 0 50%,#e6e9ed 0 75%,white 0);background-size:24px 24px}:focus-visible{outline:3px solid #bd823c;outline-offset:3px}@media(max-width:850px){.grid{grid-template-columns:1fr}main{padding:16px}}</style><main><h1>SUBJECT</h1><p class="scope">SCOPE</p><div class="grid"><section><nav><button data-bg="dark" aria-pressed="true">深底预览</button><button data-bg="white" aria-pressed="false">白底预览</button><button data-bg="checker" aria-pressed="false">透明棋盘</button><a href="__PNG__" download="wordcloud.png">保存透明PNG</a><a href="__WHITE_PNG__" download="wordcloud-white.png">保存白底PNG</a></nav>__SELECTION_NOTE__<div class="stage"><img src="__PNG__" alt="透明词云图"></div></section><section>SECTIONS</section></div></main><script>const stage=document.querySelector('.stage');document.querySelectorAll('button').forEach(b=>b.onclick=()=>{document.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));stage.classList.toggle('checker',b.dataset.bg==='checker');stage.style.backgroundColor=b.dataset.bg==='dark'?'#2c354a':'white';});</script></html>'''
    # Replace template tokens before inserting user text, preventing token recursion.
    selection_note = ('<p class="scope">图中展示词频前100项（输入共'+str(selection['input_count'])+'项）；分析使用完整词表。</p>' if selection['input_count'] > DISPLAY_LIMIT else '')
    substitutions={'__SELECTION_NOTE__':selection_note, 'SUBJECT':html.escape(ctx['subject']), 'SCOPE':html.escape(scope), '__PNG__':png, '__WHITE_PNG__':white_png, 'SECTIONS':''.join(sections)}
    page=re.sub(r'SUBJECT|SCOPE|__WHITE_PNG__|__PNG__|__SELECTION_NOTE__|SECTIONS',lambda m:substitutions[m.group()],page)
    targets=['analysis.md','preview.html','analysis-evidence.json']
    require(all(not (dest/t).exists() for t in targets),'OUTPUT_EXISTS','Analysis artifacts already exist')
    (dest/'analysis.md').write_text('\n'.join(md),encoding='utf-8')
    (dest/'preview.html').write_text(page,encoding='utf-8')
    write_json(dest/'analysis-evidence.json',dict(skill_version=VERSION,input_sha256=digest(data),topics=normalized,limitations=limits,representative_values_from_source=True,render_selection=selection,semantic_review='REQUIRED_BY_CALLING_AGENT',**({'post_review':post_review} if post_review is not None else {})))
    if limits:
        result = error_result('OBJECTIVE_INCOMPLETE', '；'.join(limits), 'compose', status='PARTIAL')
        result.update(topics=len(normalized), output=str(dest), completed_artifacts=['wordcloud.png', 'wordcloud-white.png', *targets])
        return result
    return {'status':'COMPOSED','topics':len(normalized),'output':str(dest)}


# Machine-readable actions are also documented in references/errors.md.
ERROR_ACTIONS = {}
for codes, action in [
    ('CLI_ARGUMENT', '按 --help 修正子命令、必填参数或参数类型后重试。'),
    ('READ_JSON', '检查文件路径、访问权限、UTF-8编码及JSON格式，修复后重试。'),
    ('INVALID_TEXT INPUT_SCHEMA INPUT_CONTEXT INPUT_SENTIMENT INPUT_CONTENT_TYPE INPUT_METRIC INPUT_WORDS INVALID_TERM DUPLICATE_TERM INVALID_VALUE ZERO_FREQUENCY',
     '按输入契约修正指出的字段或词频；保留原件，不自行猜值、删词或合并重复词。'),
    ('TABLE_FORMAT EMPTY_TABLE TABLE_ROW TABLE_VALUE', '检查原始表格式、表头与报错行；修复或明确转换规则后重新prepare。'),
    ('AMBIGUOUS_SHEET INVALID_SHEET AMBIGUOUS_COLUMN', '核对原表并显式指定正确的工作表及唯一词项列、数值列。'),
    ('OUTPUT_EXISTS', '使用新输出路径；compose只接续对应render目录，不覆盖已有成果。'),
    ('DEPENDENCY_MISSING ENVIRONMENT_NOT_READY', '按运行说明在授权的任务环境修复依赖或字体，再执行preflight；不修改全局Python。'),
    ('FONT_MISSING INVALID_GLYPH UNSUPPORTED_GLYPH', '指定可读取且覆盖全部入图词的字体；不删词或以方框替代。'),
    ('CANVAS_SIZE FONT_SIZE LAYOUT_CAPACITY', '检查画布与字号约束；在授权范围内调整尺寸，保留全部选定入图词。'),
    ('CLIPPED_GLYPH MASK_OVERFLOW GLYPH_OVERLAP DATA_MISMATCH', '停止交付词云，保留诊断并检查渲染实现；不得跳过完整性检查。'),
    ('INPUT_MISMATCH IMAGE_MISMATCH', '核对input、render记录与PNG是否来自同一次运行；修复关联或在新目录重绘。'),
    ('TOPIC_SCHEMA EMPTY_ANALYSIS TOPIC_TERMS UNKNOWN_TERM', '核对议题结构及原词引用并修正；不能为了通过检查编造洞察。'),
    ('POST_EVIDENCE POST_EVIDENCE_REQUIRED', '回查原帖、字段定位与阅读记录并修复；不得静默删除原帖依据后交付。'),
    ('EXECUTION_ERROR', '检查报错文件、权限、数据格式及参数；修复原因后在新输出路径重试。'),
    ('UNEXPECTED_ERROR', '保留错误类型和触发步骤交由维护者检查；不原样循环重试或宣称完成。'),
    ('INTERRUPTED', '检查中断阶段与残留文件；确认继续后使用新输出路径重试。'),
    ('OBJECTIVE_INCOMPLETE', '说明未完成目标及所需补充；保留已验证成果，补齐条件后再完成剩余部分。'),
]:
    for code in codes.split():
        ERROR_ACTIONS[code] = action


def error_result(code, message, stage, status='ERROR'):
    return dict(status=status, code=code, stage=stage, message=message,
                retryable=False, suggested_action=ERROR_ACTIONS.get(code, ERROR_ACTIONS['UNEXPECTED_ERROR']))


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise Problem('CLI_ARGUMENT', message)

def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=JsonArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('preflight');p.add_argument('--font')
    p=sub.add_parser('validate');p.add_argument('--input',required=True)
    p=sub.add_parser('prepare');p.add_argument('--table',required=True);p.add_argument('--context',required=True);p.add_argument('--output',required=True)
    for flag in ('sheet','term-column','value-column'):p.add_argument('--'+flag)
    p=sub.add_parser('render');p.add_argument('--input',required=True);p.add_argument('--out',required=True);p.add_argument('--font')
    for flag,default in [('width',1400),('height',880),('max-font-size',128),('min-font-size',10)]:p.add_argument('--'+flag,type=int,default=default)
    p=sub.add_parser('compose');p.add_argument('--input',required=True);p.add_argument('--topics',required=True);p.add_argument('--out',required=True);p.add_argument('--post-review')
    stage='arguments'
    try:
        args=parser.parse_args()
        stage=args.command
        # Keep command stdout a single JSON object even if a library prints diagnostics.
        with redirect_stdout(sys.stderr):
            if args.command=='preflight':
                found,missing=packages()
                fonts=([Path(args.font)] if args.font and Path(args.font).is_file()
                       else [] if args.font else font_candidates())
                if missing or not fonts:
                    result=error_result('ENVIRONMENT_NOT_READY', '依赖或字体未就绪；详见missing和fonts字段。', stage, status='NEEDS_SETUP')
                else:
                    result={'status':'READY'}
                result.update(python=sys.version.split()[0],packages=found,missing=missing,fonts=[str(p) for p in fonts])
            elif args.command=='validate':
                data=load_input(args.input);result={'status':'VALID','words':len(data['words']),'input_sha256':digest(data)}
            else:result={'prepare':prepare,'render':render,'compose':compose}[args.command](args)
    except Problem as e:
        result=error_result(e.code,e.message,stage)
    except ImportError as e:
        result=error_result('DEPENDENCY_MISSING',str(e),stage)
    except (OSError,ValueError,KeyError,TypeError) as e:
        result=error_result('EXECUTION_ERROR',str(e),stage)
    except KeyboardInterrupt:
        result=error_result('INTERRUPTED','执行已中断；本次输出尚未确认完整。',stage)
    except Exception as e:
        result=error_result('UNEXPECTED_ERROR',f'{type(e).__name__}: {e}',stage)
    print(json.dumps(result,ensure_ascii=False))
    return 3 if result['status']=='PARTIAL' else 2 if result['status'] in ('ERROR','NEEDS_SETUP') else 0

if __name__=='__main__':
    raise SystemExit(main())
