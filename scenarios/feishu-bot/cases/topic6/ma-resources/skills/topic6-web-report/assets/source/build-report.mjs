import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { basename, dirname, extname, isAbsolute, join, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const defaultSourcePath = [
  resolve(here, "../热点报告相关材料/热点周报-2026-W32.source.json"),
  join(here, "source.json"),
  join(here, "source.example.json")
].find(existsSync);
const sourcePath = resolve(process.argv[2] ?? defaultSourcePath ?? "");
const outputPath = resolve(process.argv[3] ?? join(here, "index.html"));
if (!sourcePath || !existsSync(sourcePath)) {
  throw new Error("Missing report source JSON. Pass its path as the first argument.");
}
const css = readFileSync(join(here, "styles.css"), "utf8");
const js = readFileSync(join(here, "app.js"), "utf8");
const blocks = JSON.parse(readFileSync(sourcePath, "utf8"));
const reportDescriptor = `${basename(sourcePath)}\n${blocks.map((block) => String(block.text ?? "")).join("\n")}`;
const hasMonthlyHint = /monthly|月刊|月报|月度|20\d{2}年\s*\d{1,2}月/iu.test(reportDescriptor);
const reportKind = hasMonthlyHint ? "monthly" : "weekly";
const reportTitle = reportKind === "monthly" ? "社媒热点月刊" : "社媒热点周刊";

const logoData = readFileSync(join(here, "bluefocus-logo-white.png")).toString("base64");
const heroBackgroundPath = join(here, "assets", "hero-social-bg.jpg");
const heroBackgroundData = existsSync(heroBackgroundPath)
  ? readFileSync(heroBackgroundPath).toString("base64")
  : "";
const heroBackgroundStyle = heroBackgroundData
  ? `.report-hero{--hero-bg-image:url("data:image/jpeg;base64,${heroBackgroundData}")}`
  : "";

function loadHeroPlatformIcon(file) {
  const svg = readFileSync(join(here, "assets", file), "utf8");
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;
}

const heroPlatformLogo = loadHeroPlatformIcon("platform-logos-combined.svg");
const rankIconData = new Map(["1", "2", "3"].map((rank) => [rank, loadHeroPlatformIcon(`rank-${rank}.svg`)]));
const titleAssetPath = [
  join(here, `title-${reportKind}.png`),
  join(here, "assets", `title-${reportKind}.png`),
  resolve(here, `../deliverables/generate-bluefocus-hotspot-report/assets/title-${reportKind}.png`)
].find(existsSync);
const titleAssetData = titleAssetPath ? readFileSync(titleAssetPath).toString("base64") : "";
const legacyCaseImageFiles = [
  { file: "case-1-car-livestream.jpg", alt: "车企直播拆车相关页面截图" },
  { file: "case-2-seedance-contest.jpg", alt: "Seedance 2.5 故事大赛相关页面截图" },
  { file: "case-3-movie-title.jpg", alt: "电影片名感叹号讨论相关页面截图" },
  { file: "case-4-brand-notice.jpg", alt: "粉笔品牌公告幽默化相关页面截图" }
];
const legacyCaseImages = legacyCaseImageFiles.map(({ file, alt }) => {
  const path = join(here, "assets", file);
  return existsSync(path)
    ? { alt, src: `data:image/jpeg;base64,${readFileSync(path).toString("base64")}` }
    : null;
});
const allowLegacyCaseImages = /^(?:热点周报-2026-W32\.source|source\.example)\.json$/u.test(basename(sourcePath));

function imageMimeType(path) {
  const extension = extname(path).toLowerCase();
  return ({
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp"
  })[extension] ?? "";
}

function localImageData(path) {
  if (!path || !existsSync(path)) return "";
  const mimeType = imageMimeType(path);
  if (!mimeType) return "";
  return `data:${mimeType};base64,${readFileSync(path).toString("base64")}`;
}

function resolveCaseImage(block, caseNumber, title) {
  for (const sourceImage of block.images ?? []) {
    const source = String(sourceImage?.src ?? "").trim();
    if (!source) continue;
    const alt = cleanText(sourceImage?.alt ?? "");
    const accessibleAlt = alt && alt !== "飞书文档 - 图片" ? alt : `${title}配图`;
    if (/^data:image\//iu.test(source)) return { alt: accessibleAlt, src: source };

    let localPath = "";
    if (/^file:\/\//iu.test(source)) {
      try {
        localPath = fileURLToPath(source);
      } catch {
        localPath = "";
      }
    } else if (!/^[a-z][a-z\d+.-]*:/iu.test(source)) {
      const candidates = isAbsolute(source)
        ? [source]
        : [resolve(dirname(sourcePath), source), resolve(here, source)];
      localPath = candidates.find(existsSync) ?? "";
    }

    const embeddedSource = localImageData(localPath);
    if (embeddedSource) return { alt: accessibleAlt, src: embeddedSource };
  }

  if (allowLegacyCaseImages) return legacyCaseImages[caseNumber - 1] ?? null;
  return null;
}
const fontVariants = [
  ["Light", 300],
  ["Regular", 400],
  ["Medium", 500],
  ["SemiBold", 600],
  ["Bold", 700]
];
const poppinsFaces = fontVariants
  .map(([style, weight]) => {
    const fontPath = [
      join(here, "assets", "fonts", `Poppins-${style}.ttf`),
      `/Users/bluefocusofscuba/Library/Fonts/Poppins-${style}.ttf`
    ].find(existsSync);
    if (!fontPath) return "";
    const data = readFileSync(fontPath).toString("base64");
    return `@font-face{font-family:"Poppins Report";src:url(data:font/ttf;base64,${data}) format("truetype");font-style:normal;font-weight:${weight};font-display:swap;}`;
  })
  .filter(Boolean)
  .join("\n");

const icons = {
  industry: '<svg viewBox="0 0 256 256" aria-hidden="true"><path d="M128,24h0A104,104,0,1,0,232,128,104.12,104.12,0,0,0,128,24Zm88,104a87.61,87.61,0,0,1-3.33,24H174.16a157.44,157.44,0,0,0,0-48h38.51A87.61,87.61,0,0,1,216,128ZM102,168H154a115.11,115.11,0,0,1-26,45A115.27,115.27,0,0,1,102,168Zm-3.9-16a140.84,140.84,0,0,1,0-48h59.88a140.84,140.84,0,0,1,0,48ZM40,128a87.61,87.61,0,0,1,3.33-24H81.84a157.44,157.44,0,0,0,0,48H43.33A87.61,87.61,0,0,1,40,128ZM154,88H102a115.11,115.11,0,0,1,26-45A115.27,115.27,0,0,1,154,88Zm52.33,0H170.71a135.28,135.28,0,0,0-22.3-45.6A88.29,88.29,0,0,1,206.37,88ZM107.59,42.4A135.28,135.28,0,0,0,85.29,88H49.63A88.29,88.29,0,0,1,107.59,42.4ZM49.63,168H85.29a135.28,135.28,0,0,0,22.3,45.6A88.29,88.29,0,0,1,49.63,168Zm98.78,45.6a135.28,135.28,0,0,0,22.3-45.6h35.66A88.29,88.29,0,0,1,148.41,213.6Z"/></svg>',
  nodes: '<svg viewBox="0 0 256 256" aria-hidden="true"><path d="M208,32H184V24a8,8,0,0,0-16,0v8H88V24a8,8,0,0,0-16,0v8H48A16,16,0,0,0,32,48V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V48A16,16,0,0,0,208,32ZM72,48v8a8,8,0,0,0,16,0V48h80v8a8,8,0,0,0,16,0V48h24V80H48V48ZM208,208H48V96H208V208Zm-96-88v64a8,8,0,0,1-16,0V132.94l-4.42,2.22a8,8,0,0,1-7.16-14.32l16-8A8,8,0,0,1,112,120Zm59.16,30.45L152,176h16a8,8,0,0,1,0,16H136a8,8,0,0,1-6.4-12.8l28.78-38.37A8,8,0,1,0,145.07,132a8,8,0,1,1-13.85-8A24,24,0,0,1,176,136,23.76,23.76,0,0,1,171.16,150.45Z"/></svg>',
  platforms: '<svg viewBox="0 0 256 256" aria-hidden="true"><path d="M128,88a40,40,0,1,0,40,40A40,40,0,0,0,128,88Zm0,64a24,24,0,1,1,24-24A24,24,0,0,1,128,152Zm73.71,7.14a80,80,0,0,1-14.08,22.2,8,8,0,0,1-11.92-10.67,63.95,63.95,0,0,0,0-85.33,8,8,0,1,1,11.92-10.67,80.08,80.08,0,0,1,14.08,84.47ZM69,103.09a64,64,0,0,0,11.26,67.58,8,8,0,0,1-11.92,10.67,79.93,79.93,0,0,1,0-106.67A8,8,0,1,1,80.29,85.34,63.77,63.77,0,0,0,69,103.09ZM248,128a119.58,119.58,0,0,1-34.29,84,8,8,0,1,1-11.42-11.2,103.9,103.9,0,0,0,0-145.56A8,8,0,1,1,213.71,44,119.58,119.58,0,0,1,248,128ZM53.71,200.78A8,8,0,1,1,42.29,212a119.87,119.87,0,0,1,0-168,8,8,0,1,1,11.42,11.2,103.9,103.9,0,0,0,0,145.56Z"/></svg>',
  findings: '<svg viewBox="0 0 256 256" aria-hidden="true"><path d="M248,124a56.11,56.11,0,0,0-32-50.61V72a48,48,0,0,0-88-26.49A48,48,0,0,0,40,72v1.39a56,56,0,0,0,0,101.2V176a48,48,0,0,0,88,26.49A48,48,0,0,0,216,176v-1.41A56.09,56.09,0,0,0,248,124ZM88,208a32,32,0,0,1-31.81-28.56A55.87,55.87,0,0,0,64,180h8a8,8,0,0,0,0-16H64A40,40,0,0,1,50.67,86.27,8,8,0,0,0,56,78.73V72a32,32,0,0,1,64,0v68.26A47.8,47.8,0,0,0,88,128a8,8,0,0,0,0,16,32,32,0,0,1,0,64Zm104-44h-8a8,8,0,0,0,0,16h8a55.87,55.87,0,0,0,7.81-.56A32,32,0,1,1,168,144a8,8,0,0,0,0-16,47.8,47.8,0,0,0-32,12.26V72a32,32,0,0,1,64,0v6.73a8,8,0,0,0,5.33,7.54A40,40,0,0,1,192,164Zm16-52a8,8,0,0,1-8,8h-4a36,36,0,0,1-36-36V80a8,8,0,0,1,16,0v4a20,20,0,0,0,20,20h4A8,8,0,0,1,208,112ZM60,120H56a8,8,0,0,1,0-16h4A20,20,0,0,0,80,84V80a8,8,0,0,1,16,0v4A36,36,0,0,1,60,120Z"/></svg>'
};

const backToTopIcon = '<svg viewBox="0 0 1024 1024" aria-hidden="true"><path d="M514.7 101.6c-228.3 0-413.3 185.1-413.3 413.3s185.1 413.3 413.3 413.3c228.2.1 413.3-185 413.3-413.2S742.9 101.6 514.7 101.6zm-129.2 218.9h246.2c11.6 0 21.1 9.2 21.1 20.6s-9.4 20.6-21.1 20.6H385.5c-11.6 0-21.1-9.2-21.1-20.6s9.4-20.6 21.1-20.6zm267.4 245.1c-10.2 9.9-26.7 9.9-36.8 0l-86.4-84.2v219.7c0 11.4-9.4 20.6-21.1 20.6-11.6 0-21.1-9.2-21.1-20.6V481.3l-86.4 84.2c-10.2 9.9-26.7 9.9-36.8 0-10.2-9.9-10.2-26 0-35.9L490.1 407c5.1-5 11.8-7.5 18.5-7.5s13.3 2.5 18.4 7.4l125.9 122.8c10.2 9.9 10.2 25.9 0 35.9z"/></svg>';

const sectionConfig = [
  { match: /^一、行业(?:分布)?及热门话题/u, id: "industry-distribution", label: "行业分布", icon: "industry", tone: "violet" },
  { match: /^二、营销节点/u, id: "marketing-nodes", label: "营销节点", icon: "nodes", tone: "orange" },
  { match: /^三、平台(?:热点|新鲜事)/u, id: "platform-hotspots", label: "平台热点", icon: "platforms", tone: "cyan" },
  { match: /^四、营销发现/u, id: "marketing-findings", label: "营销发现", icon: "findings", tone: "pink" }
];

function cleanText(value = "") {
  return String(value)
    .replace(/[\u200b\ufeff]/g, "")
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function navLabelFor(sectionTitle, summaryLabel) {
  const exactLabel = cleanText(sectionTitle).replace(/\s+/gu, "");
  return Array.from(exactLabel).length <= 5 ? exactLabel : summaryLabel;
}

function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[character]);
}

const exactSourceLinks = new Map([
  ["【TF家族练习生】《突围II破局》EP06：我们把彼此留下来（下）", "https://www.bilibili.com/video/av117024504220475/"],
  ["【TF家族练习生】《突围II破局》EP07：破局（上）", "https://www.bilibili.com/video/av117058662566209/"],
  ["【TF家族练习生】《突围II破局》EP06：加更", "https://www.bilibili.com/video/av117042422286834/"],
  ["天津人吃到美食belike", "https://www.bilibili.com/video/av117025829560104/"],
  ["东北人吃到美食be like2（喵小乐配音）", "https://www.bilibili.com/video/av117035795226078/"],
  ["周处除三害现实版", "https://www.bilibili.com/video/av117042304911508/"],
  ["我变成赖泽平最痛恨的人！【如是书院】", "https://www.bilibili.com/video/av117053327477466/"],
  ["在云南昆明盘龙区吃烟火气烧烤，感受炭火与肉的滇味暴击", "https://www.bilibili.com/video/av117025007473679/"],
  ["在云南昆明禄劝小平坝吃70块一斤的雪花牛肉，好吃到爆炸", "https://www.bilibili.com/video/av117053361102380/"],
  ["海绵宝宝大战僵尸day1，海绵宝宝射手", "https://www.bilibili.com/video/av117019705869098/"],
  ["高中尊者？本科圣人？小明修仙传25分钟优化纯享版【AI全民制作人】", "https://www.bilibili.com/video/av117046046099439/"],
  ["如何看待《蜘蛛侠4》导演否认成家班参与设计电影？", "https://www.zhihu.com/question/2068278852867166688"],
  ["《蜘蛛侠：崭新之日》大爆，前三部导演乔恩·瓦茨遭网暴，客观来说前三部质量如何？", "https://www.zhihu.com/question/2068304398212495175"],
  ["《蜘蛛侠：崭新之日》全球开画票房9.27亿美元，如何评价这一成绩？", "https://www.zhihu.com/question/2067561924846248123"],
  ["如何评价超前点映中，由文牧野执导、沈腾领衔主演的电影《欢迎来龙餐馆》？", "https://www.zhihu.com/question/2068425373046544035"],
  ["你会去电影院看沈腾主演的新电影《欢迎来龙餐馆》吗？票房能破50亿吗?", "https://www.zhihu.com/question/2068398077522793536"],
  ["《欢迎来龙餐馆》目前释出了三版预告，看完后你的直观感受是什么？", "https://www.zhihu.com/question/2068648022410356377"],
  ["《凡人修仙传》动画186集观众满意吗？", "https://www.zhihu.com/question/2068852017641132989"],
  ["《凡人修仙传》动画 186 集观众满意吗？", "https://www.zhihu.com/question/2068852017641132989"],
  ["凡人修仙传动画186集最高在线人数多少？", "https://www.zhihu.com/question/2068852278321324886"],
  ["《凡人修仙传》越看越不得劲了，你有这个感觉吗？", "https://www.zhihu.com/question/2058207404576264594"],
  ["如何看待「竹知了」视频被投诉下架的事件？", "https://www.zhihu.com/question/2066426757268480199"],
  ["鸿蒙智行回应「竹知了」事件，称从未要求下架「竹知了」商品，哪些信息值得关注？", "https://www.zhihu.com/question/2068091380799206173"],
  ["竹知了视频被投诉下架，疑因玩具与余承东声音类似，为啥没提人名和品牌也会侵权？", "https://s.weibo.com/weibo?q=%E7%AB%B9%E7%9F%A5%E4%BA%86"],
  ["如何看待DeepSeek 8月6日公告称即将大幅度涨价？", "https://www.zhihu.com/question/2068628771687625392"],
  ["怎么看DeepSeek公告显示「计划近期上调API服务的定价，预计涨幅较大」？", "https://www.zhihu.com/question/2068637938070697857"],
  ["怎么看OpenCode创始人说「DeepSeek涨价不是因为亏钱，而是为了劝退用户」？", "https://www.zhihu.com/question/2069015264461579129"],
  ["2026 上半年国内手机销量 TOP30 出炉，苹果包揽前三、华为领跑国产，哪些品牌的表现值得关注？", "https://www.zhihu.com/question/2069715027674624332"]
]);

function normalizeSourceLinkText(value) {
  return cleanText(value).replace(/^[•·-]\s*/u, "").trim();
}

function blockSourceLinks(block) {
  return new Map((block.links ?? []).map(({ text, href }) => [normalizeSourceLinkText(text), href]));
}

function renderSourceLinkedText(value, sourceLinks = exactSourceLinks) {
  const cleaned = cleanText(value);
  const parentheticalMatch = cleaned.match(/^(.*?)(\s*[（(](微博|抖音|B站|知乎|快手|头条|百度)[）)])$/u);
  const scoredPlatformMatch = cleaned.match(/^(.*?)(\s*·\s*(微博|抖音|B站|知乎|快手|头条|百度)\s*·\s*\d+(?:\.\d+)?)$/u);
  const match = parentheticalMatch ?? scoredPlatformMatch;
  const topic = normalizeSourceLinkText(match ? match[1] : cleaned);
  const platform = match?.[3];
  const fallbackLinks = {
    微博: `https://s.weibo.com/weibo?q=${encodeURIComponent(topic)}`,
    抖音: `https://www.douyin.com/search/${encodeURIComponent(topic)}`,
    B站: `https://search.bilibili.com/all?keyword=${encodeURIComponent(topic)}`,
    知乎: `https://www.zhihu.com/search?type=content&q=${encodeURIComponent(topic)}`,
    快手: `https://www.kuaishou.com/search/video?searchKey=${encodeURIComponent(topic)}`,
    头条: `https://so.toutiao.com/search?keyword=${encodeURIComponent(topic)}`,
    百度: `https://www.baidu.com/s?wd=${encodeURIComponent(topic)}`
  };
  const href = sourceLinks.get(topic) ?? exactSourceLinks.get(topic) ?? (platform ? fallbackLinks[platform] : "");
  if (!href) return escapeHTML(cleaned);
  return `<a class="source-link" href="${escapeHTML(href)}" target="_blank" rel="noopener noreferrer">${escapeHTML(topic)}</a>${match ? escapeHTML(match[2]) : ""}`;
}

function textLines(value) {
  return cleanText(value).split("\n").map((line) => line.trim()).filter(Boolean);
}

function renderPlainText(value, sourceLinks) {
  return textLines(value).map((line) => renderSourceLinkedText(line, sourceLinks)).join("<br>");
}

function renderEmphasizedLine(value) {
  const pattern = /(原始热点：|营销发现：|群体：|触发事件：|行为反应：|TOP\s*\d+|\d+\+\s*行业|\d+(?:\.\d+)?(?:\s*[-–—]\s*\d+(?:\.\d+)?)?\s*(?:%|条|分|倍|席|个|家|周)|[「“"][^」”"\n]{2,32}[」”"])/giu;
  let output = "";
  let cursor = 0;
  for (const match of String(value).matchAll(pattern)) {
    output += escapeHTML(value.slice(cursor, match.index));
    output += `<strong class="key-emphasis">${escapeHTML(match[0])}</strong>`;
    cursor = match.index + match[0].length;
  }
  return output + escapeHTML(value.slice(cursor));
}

function renderNarrativeText(value) {
  return textLines(value).map(renderEmphasizedLine).join("<br>");
}

function renderListItem(value) {
  return renderEmphasizedLine(cleanText(value).replace(/^[•·-]\s*/u, ""));
}

function sentimentClass(value) {
  const text = cleanText(value);
  if (/正向评论|正向反馈|正面评价|舆情缓和|扭转舆情|成功化解/u.test(text)) return " sentiment-positive";
  return "";
}

function heatBand(score) {
  const value = Math.max(0, Math.min(100, Number(score) || 0));
  if (value <= 10) return 0;
  if (value <= 20) return 1;
  if (value <= 30) return 2;
  if (value <= 40) return 3;
  if (value <= 50) return 4;
  if (value <= 60) return 5;
  if (value <= 70) return 6;
  if (value <= 80) return 7;
  if (value <= 90) return 8;
  return 9;
}

function renderHeat(value) {
  const score = Math.max(0, Math.min(100, Number.parseFloat(cleanText(value)) || 0));
  return `<div class="heat-meter heat-${heatBand(score)}" aria-label="标准化热度分 ${escapeHTML(cleanText(value))}"><span class="heat-fill" style="width:${score}%"><b>${escapeHTML(cleanText(value))}</b></span></div>`;
}

function renderCell(value, header, sourceLinks = exactSourceLinks) {
  const cleaned = cleanText(value);
  if (/标准化.*分|热度分|热度值/u.test(header) && /^\d+(?:\.\d+)?$/u.test(cleaned)) {
    return renderHeat(cleaned);
  }

  const lines = textLines(cleaned);
  const listItems = [];
  let consumed = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^[•·|｜]$/u.test(line) && lines[index + 1]) {
      listItems.push(lines[index + 1]);
      consumed += 2;
      index += 1;
    } else if (/^[•·\-|｜]\s*\S/u.test(line)) {
      listItems.push(line.replace(/^[•·\-|｜]\s*/u, ""));
      consumed += 1;
    }
  }
  if (listItems.length && consumed === lines.length) {
    return `<ul class="cell-list">${listItems.map((line) => `<li>${renderSourceLinkedText(line, sourceLinks)}</li>`).join("")}</ul>`;
  }
  return renderPlainText(cleaned, sourceLinks);
}

const industryBubblePalette = [
  ["#8d86ff", "#5549d8"],
  ["#71cfff", "#287fd4"],
  ["#ffb77c", "#e56d37"],
  ["#f48fbe", "#cb437f"],
  ["#ad8bff", "#7552d6"],
  ["#ff9481", "#d64b43"],
  ["#70dabe", "#249779"],
  ["#809dff", "#405dc8"],
  ["#ffd777", "#d49820"],
  ["#72d8e2", "#278fa5"]
];

function industryShortLabel(name) {
  const labels = {
    "科技/AI产业": "科技AI",
    "体育赛事": "体育",
    "食品零食": "食品",
    "汽车出行": "汽车",
    "数码3C": "数码",
    "餐饮连锁": "餐饮"
  };
  return labels[name] ?? name;
}

function renderIndustryBubbleChart(headers, body) {
  const industryIndex = headers.findIndex((header) => /TOP10行业|行业/u.test(header));
  const countIndex = headers.findIndex((header) => /热点(?:数量|总计)/u.test(header));
  const scoreIndex = headers.findIndex((header) => /中位数|热度值/u.test(header));
  if (industryIndex < 0 || countIndex < 0 || scoreIndex < 0) return "";
  const scoreAxisLabel = cleanText(headers[scoreIndex] ?? "热度值");

  const data = body.map((row, index) => ({
    industry: cleanText(row[industryIndex] ?? ""),
    count: Number.parseFloat(cleanText(row[countIndex] ?? "")),
    score: Number.parseFloat(cleanText(row[scoreIndex] ?? "")),
    gradient: industryBubblePalette[index % industryBubblePalette.length],
    gradientId: `industry-bubble-gradient-${index}`
  })).filter((item) => item.industry && Number.isFinite(item.count) && Number.isFinite(item.score));
  if (!data.length) return "";

  const width = 1040;
  const height = 306;
  const margin = { top: 34, right: 38, bottom: 62, left: 72 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const maxCount = Math.max(...data.map((item) => item.count));
  const minCount = Math.min(...data.map((item) => item.count));
  const minScore = Math.min(...data.map((item) => item.score));
  const maxScore = Math.max(...data.map((item) => item.score));
  const xMax = Math.ceil(maxCount / 50) * 50;
  const yMin = Math.floor((minScore - 2) / 2) * 2;
  const yMax = Math.ceil((maxScore + 2) / 2) * 2;
  const xTicks = Array.from({ length: xMax / 50 + 1 }, (_, index) => index * 50);
  const yTicks = Array.from({ length: (yMax - yMin) / 2 + 1 }, (_, index) => yMin + index * 2);
  const x = (value) => margin.left + (value / xMax) * plotWidth;
  const y = (value) => margin.top + ((yMax - value) / (yMax - yMin)) * plotHeight;
  const radius = (value) => {
    if (maxCount === minCount) return 21;
    const ratio = (Math.sqrt(value) - Math.sqrt(minCount)) / (Math.sqrt(maxCount) - Math.sqrt(minCount));
    return 11 + ratio * 21;
  };

  const grid = [
    ...xTicks.map((tick) => `<g class="bubble-grid-line"><line x1="${x(tick)}" y1="${margin.top}" x2="${x(tick)}" y2="${margin.top + plotHeight}"/><text x="${x(tick)}" y="${margin.top + plotHeight + 27}" text-anchor="middle">${tick}</text></g>`),
    ...yTicks.map((tick) => `<g class="bubble-grid-line"><line x1="${margin.left}" y1="${y(tick)}" x2="${margin.left + plotWidth}" y2="${y(tick)}"/><text x="${margin.left - 14}" y="${y(tick) + 4}" text-anchor="end">${tick}</text></g>`)
  ].join("");

  const bubbles = [...data]
    .sort((a, b) => b.count - a.count)
    .map((item) => {
      const cx = x(item.count);
      const cy = y(item.score);
      const r = radius(item.count);
      const labelY = Math.min(margin.top + plotHeight + 10, cy + r + 13);
      const aria = `${item.industry}，热点数量 ${item.count}，${scoreAxisLabel} ${item.score}`;
      return `<g class="industry-bubble" tabindex="0" role="img" aria-label="${escapeHTML(aria)}" data-industry="${escapeHTML(item.industry)}" data-count="${item.count}" data-score="${item.score}">
        <title>${escapeHTML(aria)}</title>
        <circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}" fill="url(#${item.gradientId})"/>
        <text class="industry-bubble-label" x="${cx.toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="middle">${escapeHTML(industryShortLabel(item.industry))}</text>
      </g>`;
    }).join("");

  const gradients = data.map((item) => `<linearGradient id="${item.gradientId}" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="${item.gradient[0]}"/><stop offset="100%" stop-color="${item.gradient[1]}"/></linearGradient>`).join("");
  const legend = data.map((item) => `<span class="industry-legend-item"><i style="--legend-start:${item.gradient[0]};--legend-end:${item.gradient[1]}"></i>${escapeHTML(item.industry)}</span>`).join("");

  return `<figure class="industry-bubble-card" data-bubble-chart>
    <figcaption class="industry-bubble-caption">
      <div><strong>行业分布气泡图</strong><span>越靠右上，热点数量越多且热度越高</span></div>
      <div class="bubble-size-guide" aria-label="气泡大小代表热点数量"><i></i><i></i><i></i><span>气泡大小 = 热点数量</span></div>
    </figcaption>
    <div class="industry-bubble-scroll" tabindex="0" role="region" aria-label="行业分布气泡图，可横向滚动查看完整图表">
      <div class="industry-bubble-stage">
        <svg class="industry-bubble-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="横轴为热点数量，纵轴为${escapeHTML(scoreAxisLabel)}的行业分布气泡图">
          <defs>${gradients}</defs>
          <rect class="bubble-hot-zone" x="${x(xMax * 0.5)}" y="${margin.top}" width="${plotWidth * 0.5}" height="${plotHeight * 0.5}" rx="18"/>
          ${grid}
          <line class="bubble-axis" x1="${margin.left}" y1="${margin.top + plotHeight}" x2="${margin.left + plotWidth}" y2="${margin.top + plotHeight}"/>
          <line class="bubble-axis" x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + plotHeight}"/>
          <text class="bubble-axis-title" x="${margin.left + plotWidth / 2}" y="${height - 16}" text-anchor="middle">热点数量</text>
          <text class="bubble-axis-title" transform="translate(20 ${margin.top + plotHeight / 2}) rotate(-90)" text-anchor="middle">${escapeHTML(scoreAxisLabel)}</text>
          <text class="bubble-hot-zone-label" x="${margin.left + plotWidth - 12}" y="${margin.top + 22}" text-anchor="end">高数量 · 高热度</text>
          ${bubbles}
        </svg>
        <div class="industry-bubble-tooltip" role="status" aria-live="polite" hidden></div>
      </div>
    </div>
    <div class="industry-bubble-legend" aria-label="行业颜色图例">${legend}</div>
  </figure>`;
}

function renderTable(block) {
  const rows = block.tables.flat().map((row) => row.map(cleanText)).filter((row) => row.some(Boolean));
  if (rows.length < 2) return "";
  const [headers, ...body] = rows;
  const heading = headers.join(" / ");
  const sourceLinks = blockSourceLinks(block);
  const chart = /TOP10行业/u.test(heading) ? renderIndustryBubbleChart(headers, body) : "";
  const rankIndex = headers.findIndex((header) => /^排名$/u.test(header));
  const eventNameIndex = headers.findIndex((header) => /事件名称/u.test(header));
  const industryIndex = headers.findIndex((header) => /^行业$/u.test(header));
  const countIndex = headers.findIndex((header) => /热点(?:数量|总计)/u.test(header));
  const heatIndex = headers.findIndex((header) => /^(?:标准化.*分|热度分|热度值|热点最高标准化分)$/u.test(cleanText(header)));
  const nodeDateIndex = headers.findIndex((header) => /^节点日期$/u.test(header));
  const nodeNameIndex = headers.findIndex((header) => /^节点名称$/u.test(header));
  const riskTypeIndex = headers.findIndex((header) => /^风险类型$/u.test(header));
  const riskEventIndex = headers.findIndex((header) => /^风险事件$/u.test(header));
  const hasNodeTitlePair = nodeDateIndex >= 0 && nodeNameIndex >= 0;
  const isHotEventTable = rankIndex === 0 && eventNameIndex > 0;
  const isRiskTable = riskTypeIndex >= 0 && riskEventIndex >= 0;
  const primaryIndex = hasNodeTitlePair
    ? nodeNameIndex
    : isRiskTable
      ? riskEventIndex
    : rankIndex === 0 && eventNameIndex > 0
      ? eventNameIndex
      : 0;
  const isMarketingFindingTable = industryIndex >= 0 && /^(?:合作事件|事件)$/u.test(cleanText(headers[primaryIndex] ?? ""));
  const isIndustryRankingTable = /TOP10行业/u.test(heading);
  const hasImplicitIndex = /TOP10行业|玩法话题|平台热点词/u.test(headers[primaryIndex] ?? "");
  const isPlayTable = /玩法话题|平台热点词/u.test(headers[primaryIndex] ?? "");
  const isPlatformKeywordTable = /平台热点词/u.test(headers[primaryIndex] ?? "");
  const longFieldPattern = /代表热搜|具体热搜|主要驱动话题|品类机会|借势方向|风险性质|风险影响/u;
  const hasLongContent = body.some((row) => row.some((value, index) => {
    const cleaned = cleanText(value);
    return (longFieldPattern.test(headers[index] ?? "") && cleaned.length > 56) || textLines(cleaned).length > 3;
  }));
  const maxRowLength = Math.max(...body.map((row) => row.reduce((total, value) => total + cleanText(value).length, 0)));
  const gridClasses = [];
  if (isHotEventTable || isPlayTable || /TOP10行业|^节点\s*\//u.test(heading) || (!hasLongContent && headers.length <= 5 && maxRowLength <= 180)) {
    gridClasses.push("data-card-grid--two");
  }
  if (isHotEventTable) gridClasses.push("data-card-grid--hot-events");
  if (isPlayTable) gridClasses.push("data-card-grid--plays");
  if (isPlatformKeywordTable) gridClasses.push("data-card-grid--platform-keywords");
  if (isRiskTable) gridClasses.push("data-card-grid--risks");
  if (rankIndex >= 0 || hasImplicitIndex) gridClasses.push("data-card-grid--ranked");
  if (isIndustryRankingTable) gridClasses.push("data-card-grid--industry-ranking");
  const gridClass = gridClasses.length ? ` ${gridClasses.join(" ")}` : "";

  let carriedRiskType = "";
  const cards = body.map((row, rowIndex) => {
    if (isRiskTable && cleanText(row[riskTypeIndex] ?? "")) carriedRiskType = cleanText(row[riskTypeIndex]);
    const primaryValue = cleanText(row[primaryIndex] ?? "") || `第 ${rowIndex + 1} 项`;
    const rank = rankIndex >= 0 ? cleanText(row[rankIndex] ?? "") : "";
    const sequence = rank || (hasImplicitIndex ? String(rowIndex + 1) : "");
    const fields = headers.map((header, index) => {
      if (index === primaryIndex || index === rankIndex || index === heatIndex || index === countIndex || (isRiskTable && index === riskTypeIndex) || ((isHotEventTable || isMarketingFindingTable) && index === industryIndex) || (hasNodeTitlePair && index === nodeDateIndex)) return "";
      const value = row[index] ?? "";
      const cleaned = cleanText(value);
      if (!cleaned) return "";
      if (/所属事件/u.test(header) && cleaned === primaryValue) return "";
      const fieldClasses = ["data-card-field"];
      if (longFieldPattern.test(header) || textLines(cleaned).length > 2 || cleaned.length > 72) fieldClasses.push("data-card-field--wide");
      if (/借势方向/u.test(header)) fieldClasses.push("data-card-field--direction");
      if (/本周主要驱动话题|主要驱动话题/u.test(header)) fieldClasses.push("data-card-field--drivers");
      if (/本周主要驱动话题|主要驱动话题|代表热搜|具体热搜/u.test(header) && textLines(cleaned).length > 1) fieldClasses.push("data-card-field--list-grid");
      return `<div class="${fieldClasses.join(" ")}"><dt>${escapeHTML(header)}</dt><dd>${renderCell(value, header, sourceLinks)}</dd></div>`;
    }).join("");
    const rankIcon = /玩法话题/u.test(headers[primaryIndex] ?? "") ? null : rankIconData.get(sequence);
    const sequenceBadge = rankIcon
      ? `<span class="data-card-award" aria-label="第 ${escapeHTML(sequence)} 名"><img src="${rankIcon}" alt=""></span>`
      : sequence
        ? `<span class="data-card-index" data-rank="${escapeHTML(sequence)}"><span>${escapeHTML(sequence)}</span></span>`
        : "";
    const kickerText = isRiskTable ? carriedRiskType || "风险事件" : headers[primaryIndex] ?? "";
    const kicker = sequence || hasNodeTitlePair ? "" : `<span class="data-card-kicker">${escapeHTML(kickerText)}</span>`;
    const title = hasNodeTitlePair
      ? `${escapeHTML(primaryValue)}<time datetime="${escapeHTML(cleanText(row[nodeDateIndex] ?? ""))}">${escapeHTML(cleanText(row[nodeDateIndex] ?? ""))}</time>`
      : escapeHTML(primaryValue);
    const hotEventIndustry = isHotEventTable && industryIndex >= 0 ? cleanText(row[industryIndex] ?? "") : "";
    const headingTitle = hotEventIndustry
      ? `${escapeHTML(hotEventIndustry)}<span class="data-card-title-separator">丨</span>${title}`
      : title;
    const industryTag = "";
    const marketingFindingIndustry = isMarketingFindingTable && cleanText(row[industryIndex] ?? "")
      ? `${cleanText(row[industryIndex])}${/行业$/u.test(cleanText(row[industryIndex])) ? "" : "行业"}`
      : "";
    const titleLabels = isMarketingFindingTable
      ? (marketingFindingIndustry ? `<div class="data-card-label-row"><span class="data-card-industry">${escapeHTML(marketingFindingIndustry)}</span></div>` : "")
      : kicker;
    const count = countIndex >= 0 && cleanText(row[countIndex] ?? "")
      ? `<div class="data-card-stat"><span>${escapeHTML(headers[countIndex])}</span><strong>${escapeHTML(cleanText(row[countIndex]))}</strong></div>`
      : "";
    const heat = heatIndex >= 0 && cleanText(row[heatIndex] ?? "")
      ? `<div class="data-card-heat"><span>${escapeHTML(headers[heatIndex])}</span>${renderCell(row[heatIndex], headers[heatIndex], sourceLinks)}</div>`
      : "";
    const meta = count || heat ? `<div class="data-card-meta">${count}${heat}</div>` : "";
    const fieldMarkup = fields ? `<dl class="data-card-fields">${fields}</dl>` : "";
    return `<article class="data-card" role="listitem"><header class="data-card-header">${sequenceBadge}<div class="data-card-title">${titleLabels}<div class="data-card-title-row"><h3>${headingTitle}</h3>${industryTag}</div></div>${meta}</header>${fieldMarkup}</article>`;
  }).join("");

  return `${chart}<div class="data-card-grid${gridClass}" role="list" aria-label="${escapeHTML(heading)}">${cards}</div>`;
}

function minorIcon(title) {
  if (/节点/u.test(title)) return "📅";
  if (/热点事件|热门事件/u.test(title)) return "🔥";
  if (/热门玩法|平台热点词/u.test(title)) return "✨";
  if (/代言/u.test(title)) return "🤝";
  if (/联名/u.test(title)) return "🎮";
  if (/争议|风险|危机/u.test(title)) return "⚠️";
  if (/洞察/u.test(title)) return "💡";
  if (/跨平台/u.test(title)) return "🔗";
  if (/食品/u.test(title)) return "🍽️";
  if (/高管/u.test(title)) return "💬";
  if (/政策/u.test(title)) return "📋";
  return "◆";
}

function isMinorHeading(text) {
  return /^(行业洞察：|(?:热点事件|热门事件)\s*TOP\s*\d+|(?:热门玩法|平台热点词)\s*TOP\s*\d+|新代言官宣|游戏\s*IP\s*联名|合作争议|政策性风险|食品安全类|高管言论类|品牌危机公关案例|跨平台扩散信号|洞察\s*\d+[：:])/u.test(text);
}

function renderGridCase(block) {
  const lines = textLines(block.text).filter((line) => line !== "•");
  const [title = "", ...details] = lines;
  const caseNumber = Number.parseInt(title.match(/^案例\s*(\d+)/u)?.[1] ?? "0", 10);
  const image = resolveCaseImage(block, caseNumber, title);
  if (!image) {
    throw new Error(`Missing reusable image for ${title || `case ${caseNumber || "?"}`}. Replace blob/remote images.src with a data URL, file:// URL, absolute path, or a path relative to ${sourcePath}.`);
  }
  const media = image
    ? `<figure class="case-visual"><img src="${image.src}" alt="${escapeHTML(image.alt)}" loading="lazy" decoding="async"></figure>`
    : "";
  const cardClass = image ? "case-card case-card--visual" : "case-card case-card--text-only";
  return `<article class="${cardClass}"><div class="case-copy"><h3>${escapeHTML(title)}</h3>${details.map((line) => `<p>${renderEmphasizedLine(line)}</p>`).join("")}</div>${media}</article>`;
}

function renderFlow(items) {
  let output = "";
  let listItems = [];
  let caseItems = [];

  const flushList = () => {
    if (!listItems.length) return;
    output += `<ul class="insight-list">${listItems.map((item) => `<li class="${sentimentClass(item).trim()}">${renderListItem(item)}</li>`).join("")}</ul>`;
    listItems = [];
  };

  const flushCases = () => {
    if (!caseItems.length) return;
    output += `<div class="case-grid">${caseItems.join("")}</div>`;
    caseItems = [];
  };

  items.forEach((block) => {
    let text = cleanText(block.text);
    if (/docx-text-block/u.test(block.className)) text = text.replace(/^•\s*/u, "");
    if (!text && !block.tables.length) return;

    if (/docx-heading3-block/u.test(block.className)) {
      flushList();
      flushCases();
      output += `<h3 class="minor-heading"><span aria-hidden="true">${minorIcon(text)}</span>${escapeHTML(text)}</h3>`;
      return;
    }

    if (/docx-bullet-block/u.test(block.className)) {
      flushCases();
      listItems.push(text);
      return;
    }

    flushList();

    if (/docx-table-block/u.test(block.className)) {
      flushCases();
      output += renderTable(block);
      return;
    }

    if (/docx-grid-block/u.test(block.className)) {
      caseItems.push(renderGridCase(block));
      return;
    }

    flushCases();
    if (isMinorHeading(text)) {
      output += `<h3 class="minor-heading"><span aria-hidden="true">${minorIcon(text)}</span>${escapeHTML(text)}</h3>`;
    } else if (/^⚠️/u.test(text)) {
      output += `<p class="caution-note">${renderNarrativeText(text)}</p>`;
    } else {
      output += `<p>${renderNarrativeText(text)}</p>`;
    }
  });

  flushList();
  flushCases();
  return output;
}

function splitSubsections(items) {
  const prelude = [];
  const groups = [];
  let current = null;

  items.forEach((block) => {
    if (/docx-heading2-block/u.test(block.className)) {
      current = { title: cleanText(block.text), blocks: [] };
      groups.push(current);
    } else if (current) {
      current.blocks.push(block);
    } else {
      prelude.push(block);
    }
  });

  return { prelude, groups };
}

function behaviorInsightIcon(title) {
  if (/仪式感|奶茶/u.test(title)) return "🥤";
  if (/消费压力|性价比/u.test(title)) return "💸";
  if (/适老|老人/u.test(title)) return "📱";
  return "💡";
}

function renderBehaviorInsights(items) {
  const intro = [];
  const groups = [];
  let current = null;

  items.forEach((block) => {
    const text = cleanText(block.text);
    if (!/docx-bullet-block/u.test(block.className) && /^洞察\s*\d+[：:]/u.test(text)) {
      current = { title: text, blocks: [] };
      groups.push(current);
    } else if (current) {
      current.blocks.push(block);
    } else {
      intro.push(block);
    }
  });

  const cards = groups.map((group, index) => `<article class="behavior-insight-card insight-tone-${(index % 3) + 1}">
      <header><span class="behavior-insight-icon" aria-hidden="true">${behaviorInsightIcon(group.title)}</span><h3>${renderEmphasizedLine(group.title)}</h3></header>
      <div class="behavior-insight-body">${renderFlow(group.blocks)}</div>
    </article>`).join("");

  return `${renderFlow(intro)}${cards ? `<div class="behavior-insight-grid">${cards}</div>` : ""}`;
}

function renderRiskCards(items) {
  const intro = [];
  const groups = [];
  let current = null;

  items.forEach((block) => {
    const text = cleanText(block.text);
    const startsGroup = /docx-heading3-block/u.test(block.className) || isMinorHeading(text);

    if (startsGroup) {
      current = { title: text, blocks: [] };
      groups.push(current);
    } else if (current) {
      current.blocks.push(block);
    } else {
      intro.push(block);
    }
  });

  const cards = groups.map((group) => `<section class="risk-topic-card">
      <h3 class="minor-heading"><span aria-hidden="true">${minorIcon(group.title)}</span>${escapeHTML(group.title)}</h3>
      ${renderFlow(group.blocks)}
    </section>`).join("");

  return `${renderFlow(intro)}${cards ? `<div class="risk-topic-grid">${cards}</div>` : ""}`;
}

function subsectionClasses(title) {
  const classes = ["subsection-card"];
  if (/微博|抖音|B\s*站|知乎/u.test(title)) classes.push("platform-card");
  if (/品牌舆情风险/u.test(title)) classes.push("risk-card");
  if (/营销案例|营销观察/u.test(title)) classes.push("marketing-case-section");
  if (/消费(?:者行为)?洞察/u.test(title)) classes.push("behavior-insights-section");
  return classes.join(" ");
}

function renderSubsection(group, options = {}) {
  const content = /消费(?:者行为)?洞察/u.test(group.title)
    ? renderBehaviorInsights(group.blocks)
    : /品牌舆情风险/u.test(group.title)
      ? renderRiskCards(group.blocks)
      : renderFlow(group.blocks);
  const classes = [subsectionClasses(group.title), options.className].filter(Boolean).join(" ");
  const attributes = options.attributes ? ` ${options.attributes}` : "";
  return `<article class="${classes}"${attributes}><h2>${escapeHTML(group.title)}</h2>${content}</article>`;
}

const platformDefinitions = [
  { key: "weibo", label: "微博", match: /微博/u },
  { key: "douyin", label: "抖音", match: /抖音/u },
  { key: "bilibili", label: "B站", match: /B\s*站/u },
  { key: "zhihu", label: "知乎", match: /知乎/u }
];

function platformDefinition(title) {
  return platformDefinitions.find((platform) => platform.match.test(title));
}

function renderPlatformTabs(items) {
  const { prelude, groups } = splitSubsections(items);
  const platformGroups = groups.map((group) => ({ group, platform: platformDefinition(group.title) })).filter(({ platform }) => platform);
  if (!platformGroups.length) return renderBlockRange(items);

  const tabs = platformGroups.map(({ platform }, index) => `<button class="platform-tab" id="platform-tab-${platform.key}" type="button" role="tab" aria-selected="${index === 0}" aria-controls="platform-panel-${platform.key}" tabindex="${index === 0 ? "0" : "-1"}" data-platform="${platform.key}">${platform.label}</button>`).join("");
  const panels = platformGroups.map(({ group, platform }, index) => renderSubsection(group, {
    className: `platform-panel${index === 0 ? " is-active" : ""}`,
    attributes: `id="platform-panel-${platform.key}" role="tabpanel" aria-labelledby="platform-tab-${platform.key}" tabindex="0"${index === 0 ? "" : " hidden"}`
  })).join("");
  const remaining = groups.filter((group) => !platformDefinition(group.title)).map((group) => renderSubsection(group)).join("");

  return `${renderFlow(prelude)}<div class="platform-tabs" data-tab-group>
    <div class="platform-tablist" role="tablist" aria-label="平台热点切换">${tabs}</div>
    <div class="platform-panels">${panels}</div>
  </div>${remaining}`;
}

function renderBlockRange(items) {
  const { prelude, groups } = splitSubsections(items);
  return `${renderFlow(prelude)}${groups.map((group) => renderSubsection(group)).join("")}`;
}

const introBlocks = blocks.slice(0, blocks.findIndex((block) => /docx-heading1-block/u.test(block.className)));
const declaration = cleanText(introBlocks.find((block) => /quote_container/u.test(block.className))?.text ?? "");
const specialNote = cleanText(introBlocks.find((block) => /^⚠️/u.test(cleanText(block.text)))?.text ?? "");
const sampleMatch = declaration.match(/数据样本：(\d+)\s*条原始热点\s*\/\s*(\d+)\s*条营销可用（([\d.]+)%）/u);
const platformMatch = declaration.match(/平台分布：([^\n]+)/u);
const periodMatch = declaration.match(/数据周期：([^（\n]+)（([^）]+)）/u);

const rawCount = sampleMatch?.[1] ?? "3631";
const usableCount = sampleMatch?.[2] ?? "1681";
const usableRate = sampleMatch?.[3] ?? "46.3";
const periodCode = cleanText(periodMatch?.[1] ?? "2026-W32");
const periodRange = cleanText(periodMatch?.[2] ?? "2026-08-03 ~ 2026-08-09").replace(/\s*~\s*/u, " 至 ");
const monthlyPeriodMatch = periodCode.match(/(20\d{2})(?:年|[-./])\s*(\d{1,2})(?:月)?/u);
const displayPeriod = reportKind === "monthly" && monthlyPeriodMatch
  ? `${monthlyPeriodMatch[1]}·${monthlyPeriodMatch[2].padStart(2, "0")}`
  : periodCode.replace("-", "·");

const sectionStarts = blocks
  .map((block, index) => ({ block, index }))
  .filter(({ block }) => /docx-heading1-block/u.test(block.className));

const navLabels = new Map();
const renderedSections = sectionStarts.map(({ block, index }, sectionIndex) => {
  const config = sectionConfig.find((item) => item.match.test(cleanText(block.text)));
  if (!config) return "";
  const end = sectionStarts[sectionIndex + 1]?.index ?? blocks.length;
  const body = blocks.slice(index + 1, end).filter((item) => !/docx-divider-block|docx-quote_container-block/u.test(item.className));
  const sectionTitle = cleanText(block.text).replace(/^[一二三四五六七八九十]+、\s*/u, "");
  navLabels.set(config.id, navLabelFor(sectionTitle, config.label));
  const bodyMarkup = config.id === "platform-hotspots" ? renderPlatformTabs(body) : renderBlockRange(body);
  return `<section class="report-section tone-${config.tone}" id="${config.id}" data-section>
    <header class="section-heading">
      <span class="section-icon" aria-hidden="true">${icons[config.icon]}</span>
      <h1>${escapeHTML(sectionTitle)}</h1>
    </header>
    <div class="section-body">${bodyMarkup}</div>
  </section>`;
}).join("\n");

const nav = sectionConfig.map((item) => `<a class="toc-link" href="#${item.id}" data-target="${item.id}"><span class="toc-icon" aria-hidden="true">${icons[item.icon]}</span><span>${escapeHTML(navLabels.get(item.id) ?? item.label)}</span></a>`).join("");

const declarationItems = declaration
  .split("\n")
  .map(cleanText)
  .filter((line) => line && line !== "📌 数据声明" && line !== "•")
  .filter((line) => !/^数据样本：|^数据周期：/u.test(line))
  .map((line) => line.replace("所有展示热度分", "所有热度分"));
const specialNoteBody = specialNote.replace(/^⚠️\s*本周特殊说明：?/u, "");
const footerNoteText = cleanText([...blocks].reverse().find((block) => /quote_container/u.test(block.className))?.text ?? specialNoteBody);
const footerNoteMatch = footerNoteText.match(/^([^：:]+[：:])\s*([\s\S]*)$/u);
const footerNoteLabel = footerNoteMatch?.[1] ?? "本周特殊说明：";
const footerNoteBody = footerNoteMatch?.[2] ?? footerNoteText ?? "";
const titleMarkup = titleAssetData
  ? `<h1 class="hero-title-image"><img src="data:image/png;base64,${titleAssetData}" alt="${escapeHTML(reportTitle)}"></h1>`
  : `<h1 class="hero-wordmark">${escapeHTML(reportTitle)}</h1>`;

const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="${escapeHTML(reportTitle)} · ${escapeHTML(displayPeriod)}">
  <meta name="theme-color" content="#433df3">
  <title>${escapeHTML(reportTitle)} · ${escapeHTML(displayPeriod)}</title>
  <style>${poppinsFaces}\n${heroBackgroundStyle}\n${css}</style>
</head>
<body>
  <div class="reading-progress" aria-hidden="true"></div>
  <a class="skip-link" href="#report-content">跳至报告正文</a>

  <header class="report-hero" id="top">
    <div class="hero-inner">
      <img class="hero-logo" src="data:image/png;base64,${logoData}" width="480" height="72" alt="BlueFocus AI">
      <div class="hero-heading">
        ${titleMarkup}
        <div class="hero-period-block">
          <div class="hero-period">
            <strong>${escapeHTML(displayPeriod)}</strong>
            <span>${escapeHTML(periodRange)}</span>
          </div>
          <div class="hero-platform-icons" tabindex="0" aria-label="数据范围：微博、知乎、抖音、B站" data-tooltip="数据范围：微博、知乎、抖音、B站">
            <img class="hero-platform-logo-strip" src="${heroPlatformLogo}" alt="微博、抖音、B站、知乎">
          </div>
        </div>
      </div>
    </div>
  </header>

  <div class="nav-shell" data-sticky-nav>
    <nav class="toc" aria-label="报告主导航">${nav}</nav>
  </div>

  <main class="content-shell" id="report-content">
    ${renderedSections}
  </main>

  <aside class="footer-note" aria-label="${escapeHTML(footerNoteLabel.replace(/[：:]$/u, ""))}"><strong>${escapeHTML(footerNoteLabel)}</strong><span>${escapeHTML(footerNoteBody)}</span></aside>
  <footer class="report-footer">Copyright@Bluefocus, 2026</footer>
  <a class="back-to-top" href="#top" aria-label="返回页面顶部" title="返回顶部">${backToTopIcon}</a>
  <script>${js}</script>
</body>
</html>`;

writeFileSync(outputPath, html, "utf8");
console.log(`Generated ${outputPath}`);
console.log(`Source: ${sourcePath}`);
console.log(`Sections: ${sectionStarts.length}`);
