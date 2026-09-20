"""只负责提示词装配与各阶段JSON Schema，不调用任何模型。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


STAGES = ('rewrite', 'titles', 'human')


def schema_for(stage: str, count: int) -> dict:
    properties = {'report': {'type': 'string'}}
    if stage == 'titles':
        properties.update(titles={'type': 'array', 'items': {'type': 'string'}, 'minItems': count, 'maxItems': count},
                          recommended_index={'type': 'integer', 'minimum': 0, 'maximum': count - 1})
    else:
        properties['body'] = {'type': 'string'}
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def build_prompt(path: Path, stage: str, read_json) -> str:
    inputs = read_json(path / 'input.json')
    snap = path / 'snapshot'
    text = lambda key: (snap / (key + '.md')).read_text(encoding='utf-8')
    common = {'手动赛道': inputs['mode'], '主题（不可篡改）': inputs['topic'],
              '写作要求与立场（不可篡改）': inputs['brief'], '账号定位、读者与作者声音': inputs['positioning'],
              '标题数量': inputs['title_count']}
    chunks = [text('boundary'), text('adapter'), '\n【本阶段】' + stage,
              '【本步骤是否允许联网】' + ('是' if stage == 'rewrite' else '否'),
              '【当前日期】' + dt.datetime.now().astimezone().isoformat(timespec='seconds'),
              '【核心提示词全文开始】\n' + text(stage) + '\n【核心提示词全文结束】',
              '【输入参数 JSON】\n' + json.dumps(common, ensure_ascii=False),
              '【本赛道人设全文（身份、语气、读者约束；不启动其中另一套流程）】\n' + text('persona')]
    if stage == 'rewrite':
        chunks += ['【参考原文，仅借鉴结构与节奏，不作为新主题事实】\n' + inputs['source'],
                   '输出 JSON：report 保存完整解构表、联网素材简报（至少3条资料，来源链接与日期）、传播力自评；'
                   'body 单独保存800–1500字仿写正文，不带报告或文章标题。必须实际调用联网搜索。']
    else:
        rewrite = read_json(path / 'rewrite.json')
        chunks += ['【仿写正文】\n' + rewrite['body'], '【已有素材与来源报告】\n' + rewrite['report']]
        if stage == 'titles':
            chunks += ['【该赛道参考标题全文】\n' + text('references'),
                       '输出 JSON：report 按原提示词完整保存 M1→M7（含所有表格、词库、逐条注释评分、Top推荐和资产包），'
                       '不允许仅写总结。titles 提取M5精修后的全部主标题，顺序对应原编号，恰好' + str(inputs['title_count']) +
                       '条纯标题。每条严格20–28个非空白字符，逐条实际计数，不能只在报告里声称合格；'
                       '请先构造信息完整、自然的20–28字标题；正常中文标点也计入字符数。不得为了达标在句尾堆叠啊、吧、呢、呀、其实吧或多余句点。'
                       '太短时补充具体对象、场景或收益，不添加无意义尾巴。'
                       'M5最终标题须与titles数组逐字一致。不得混入方向、评分、确认行。recommended_index 为Top1在titles里的0起始下标。'
                       'A/B仅按原提示词输入条件启用；额外风格包与资产留在report，不替代主标题。'
                       '数量较小时若每桶至少6条与35%上限无法同时成立，report 明示此数学冲突，优先保证总数、分布多样与35%上限。']
        else:
            titles = read_json(path / 'titles.json')
            chunks += ['【备选标题】\n' + json.dumps(titles['titles'], ensure_ascii=False),
                       '【推荐标题】' + titles['titles'][titles['recommended_index']],
                       '输出 JSON：body 为800–1500字人味终稿正文，不带标题或报告；report 保存逐项自检结果及必要修改说明。'
                       '保留已核实事实、出处与用户立场，不再联网。人设中的其他工作流不重复执行。'
                       '原提示词的生活细节要求不可用于捏造亲测、采访、价格、统计或消息；个人场景只能采用用户明确提供的真实经历或输入材料。'
                       '没有材料就删去个人场景，禁止用“设想、想象、假设”等标签包装虚构经历，也不得用随意具体数字替换模糊事实。'
                       '人味提示词全程第一人称要求用于本次终稿，原文结构借鉴不得反转用户立场。']
    return '\n\n'.join(chunks)
