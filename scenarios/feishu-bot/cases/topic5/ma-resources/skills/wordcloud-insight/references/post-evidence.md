# 原帖证据契约

有原帖时，在按[抽样规则](posts.md)阅读全文后使用本契约。正常业务输出不展示以下内部记录。新任务使用schema_version=2；脚本可读取旧版schema1，但只按旧能力核验并标legacy，不作为新证据规则已通过的依据。

## 按判断选择原句

先明确该帖支持、反驳或限定什么判断，再取最短且完整的对应原句，必要时连同上下文；保留否定、条件、比较对象和“希望”等语气。禁止默认取正文前N字。一个判断可由多字段联合支持，一个帖子可有多条证据；每段独立关联claim，不能把不连续片段拼成一段原话。

例：判断“观众希望品牌赞助”，应摘“ps：希望立白洗衣粉能给邱瑞赞助哈哈哈哈”，而非开头的观演体验。该句只支持观众愿望，不支持已合作。补读反例也保存原句及其如何修正结论。

## 字段与归属

正文text、标题title、封面OCR ocr_text分别保留原始文本，不互相拼接或用摘要替换。按实际原件选取字段，有多个OCR来源应分记录明确定位，不伪装成一个原文单元。只使用本地已有OCR，不新增识图/抓取服务。

正文可证明表达的体验；标题可补充讨论对象；OCR中的包装或商品页可辅助品牌/系列归属，但营销宣称不是用户体验或已验证性能。OCR含糊或与正文冲突时，不凭它确定品牌、系列或效果。模型核查多字段是否属于同一帖、能否共同支持claim；不把表面同一品牌的不同帖子拼成一个人的经历。

## posts.json

```json
{
  "schema_version": 2,
  "source_files": [{"path": "原帖.xlsx", "sha256": "原文件SHA256"}],
  "scope": "对象、平台、时段、已有筛选与互动量定义",
  "posts": [{
    "id": "row-20",
    "text": "看着洗衣液香买的洗衣粉，臭死了",
    "title": "臭死了这洗衣粉",
    "ocr_text": "立白大师香氛洗衣粉",
    "source_locator": "原文内容!第20行",
    "scope_status": "in_scope",
    "field_sources": {
      "text": {"source_index": 0, "sheet": "原文内容", "cell": "D20"},
      "title": {"source_index": 0, "sheet": "原文内容", "cell": "C20"},
      "ocr_text": {"source_index": 0, "sheet": "原文内容", "cell": "U20"}
    }
  }]
}
```

例中文本与定位仅示意；实际必须由原文件读取。保留id/text/source_locator/scope_status；title、ocr_text、interactions、author_id、url、published_at有则保留。field_sources为需核验字段提供机器定位，source_index从0起引用source_files。未读原帖可先只保存text，使用标题/OCR时再补齐对应完整字段及定位。

支持的定位：
- XLSX：sheet＋cell（单个单元格）；仅只读数据缓存，不执行公式。
- CSV：row＋column，均从1起且含标题行；编码utf-8-sig，默认逗号，可指定单字符delimiter。
- JSON：pointer，按JSON Pointer定位字符串，数组从0起，例如/records/0/text，支持~0及~1转义。
- TXT/MD：line_start＋line_end，从1起；包含首尾行并以换行连接，末尾文件换行不包含在字段内。

字段须与原位置的字符串逐字一致，不把截断文本补成完整原帖。Excel/CSV同一帖子涉及的字段须来自同一行及文件；JSON须由模型核对记录归属。其它格式先用授权工具读取，不能为通过定位检查伪造源文件。

## post-review.json

```json
{
  "schema_version": 2,
  "input_sha256": "validate返回的指纹",
  "posts_file": "posts.json",
  "posts_sha256": "posts.json文件SHA256",
  "selection_note": "检索词组、互动排序、多样性与跳过情况",
  "read_posts": [{
    "post_id": "row-20",
    "phase": "supplemental",
    "note": "已读提供的完整正文；该反例修正一味好闻的初判。",
    "evidence": [
      {"field": "text", "excerpt": "看着洗衣液香买的洗衣粉，臭死了", "claim": "该帖存在跨品类香味期待落差", "relation": "supports"},
      {"field": "ocr_text", "excerpt": "立白大师香氛洗衣粉", "claim": "该帖关联的产品是立白大师香氛洗衣粉", "relation": "context"}
    ]
  }],
  "stopping_reason": "实际停止原因及未解决的歧义"
}
```

文件路径相对各自JSON文件；phase为initial/supplemental。每个实际阅读记录提供至少一段evidence，field只能是text/title/ocr_text；relation为supports/contradicts/qualifies/context。excerpt为该字段的连续原文，claim明确其支撑的判断。跳过或排除的帖子可用context/qualifies记录理由，不强行作为最终洞察依据。

topics.json引用原帖时填写post_ids，并在rationale对应具体判断和反例；复用已读帖子不重复计阅读数。compose增加--post-review post-review.json。

程序检查文件指纹、原始字段定位、完整字段与源值一致、摘录逐字匹配、ID与引用关系；内部analysis-evidence.json保存证据及定位。程序不判断语义蕴含，也不能证明Agent实际读完。模型交付前复核：摘录是否直达判断、限定词是否保留、OCR角色是否恰当、多字段归属是否一致。正常完成仍不输出独立分析说明。

