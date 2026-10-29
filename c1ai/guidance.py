"""Client-neutral editing guidance; advisory, never a second execution engine."""
from copy import deepcopy

from .contracts import MODULES

ORDER = ('white_balance', 'exposure', 'hdr', 'levels', 'curves', 'color_mixer', 'color_balance')


def workflow():
    return {
        'scope': '用户已经打开并选定的Session及照片集合；默认不切换文档、不导出。',
        'transport': '本机MCP stdio或JSON CLI；不绑定模型、账号或聊天。',
        'analysis_first': '先context、read(fields=[])、preview(kind=native)，实际看图后区分可见问题、用户风格要求和不确定判断。可使用客户端已有的照片分析技能；本接口不依赖某个专属技能。',
        'candidate': '确认照片身份；只编辑用户指定或已登记的候选副本，必要时create_candidate。保留原变体，避免重复创建副本。',
        'module_order': list(ORDER),
        'module_loop': '仅选择有明确目的的模块：read(module) → adjust(module,绝对目标值) → 检查回读 → native预览。一个模块的相关参数合并请求；同模块多次技术调用可合并到一次预览。无需遍历所有模块。',
        'plan': '每个模块先说明画面依据、要改善的区域、目标及预计副作用；不要为照片自动套入固定参数或把API上限当作建议值。',
        'local_after_global': '先完成全局预览复核。仅在剩余问题局限于主体或背景时加蒙版；检查灰度蒙版覆盖，再用同一read/adjust接口调当前层。',
        'review': '用相同kind和max_size比较已实际查看的前后预览，检查主体层次、肤色、明暗细节、色彩关系及局部边缘。变亮或饱和度更高不自动代表更好。',
        'raw_limits': 'RAW通常有调整空间；渲染预览中的纯白/纯黑不能单独证明RAW传感器已经剪切，也不能保证细节一定可恢复。',
        'stop': '达到用户目标且没有明确剩余问题就停止。没有可解释的收益不继续试数值；方向不符时用新观察凭据恢复该模块已保存的值，或有意粘贴保存的完整模块快照。',
        'evidence': '参数回读成功只证明执行结果；必须实际看到新预览才能判断画面。图片回传不可用时说明缺口，不声称已看图或已改善。预览缓存最多8张，重要的前态需客户端在收到时保留。',
        'errors': '目标或观察过期则重新读取。写入超时/outcome_unknown先查原request_id的operations，禁止换编号重复执行。不要自动改用预设、UIA、截图识色或键鼠。',
        'excluded': ['清晰度', '锐化', '降噪', '镜头与几何调整', '人像精修', '导出'],
    }


_MODULE_GUIDANCE = {
    'film_grain': {'units': 'impact/granularity 0..100; native grain type enum',
        'use_when': '用户要求胶片颗粒；只在背景层调整。',
        'review': '查看原生导出的实际质感；低分辨率预览不能证明100%原尺寸颗粒外观。'},
    'white_balance': {
        'units': {'temperature': 'K', 'tint': 'C1色调数值'},
        'use_when': '有整体偏色或用户指定色温倾向；保留有意的环境光色，不强行把所有中性物体调成灰。',
        'review': '同时看肤色、背景中性色和环境氛围，避免用白平衡修复单个物体的颜色。'},
    'exposure': {
        'units': {'exposure': 'EV', 'contrast': 'C1数值', 'brightness': 'C1数值', 'saturation': 'C1数值'},
        'use_when': '先判断主体明暗关系和全局亮度，保持与画面意图相符的反差。',
        'review': '高光细节、暗部层次和主体立体感；RAW宽容度不等于必须提亮阴影。'},
    'hdr': {
        'units': 'C1高光/阴影/白色/黑色数值；不是EV或输出亮度百分比。',
        'use_when': '全局曝光后仍有明确亮部或暗部层次问题。',
        'review': '避免把所有区域拉到相近亮度，注意高光灰化和阴影过亮。'},
    'levels': {
        'units': '输入/输出端点0..255；midtone为-1..1的C1数值，不是gamma。',
        'use_when': '需要调整整体黑白点或色彩通道端点。',
        'review': '不要仅为填满直方图剪掉细节；输入黑点必须小于白点。'},
    'curves': {
        'units': '每个点[x,y]均为0..1；提交的是所选通道的完整控制点列表。',
        'use_when': '需要定形影调或通道色彩；先读取并保留不需改变的控制点。',
        'review': '观察相邻影调过渡，避免无目的增加控制点或用大幅S曲线代替曝光判断。'},
    'color_mixer': {
        'units': 'Basic/Advanced/Skin量程不同；RGB为0..255的C1原生Device RGB，不能把预览sRGB采样视为精确原生拾色。',
        'use_when': '特定颜色需要改变。优先读取已有色区，使用Basic明确色区或按原生index修改Advanced/Skin。',
        'review': '检查肤色与附近相近颜色是否一起改变；均匀度和饱和度不应默认增加。新增色区需复核覆盖与实际效果。'},
    'color_balance': {
        'units': 'x/y为C1色轮坐标，半径≤50；亮度为-50..50，主轮不接受亮度。',
        'use_when': '全局影调和物体颜色合理后，按用户意图处理阴影、中间调或高光色彩关系。',
        'review': '保留中性色、肤色和已有光色，不默认做青橙分离。'},
    'layers': {
        'units': 'opacity为0..100；身份使用完整layer_ref。',
        'use_when': '只有局部问题或有意分开调整时创建图层；先检查是否已有合适图层。',
        'review': '切换图层后重新read相应模块，确认target.layer；只按创建记录删除本工具创建的层。'},
    'masks': {
        'units': '渐变坐标按RAW原始宽高归一化；预览坐标先map_points，不能直接除以预览宽高。',
        'use_when': '全局调整后问题仍局限于某一区域；主体/背景、渐变或亮度范围按实际目标选择。',
        'review': 'kind=mask查看整层灰度覆盖，随后kind=native检查局部效果和边缘；灰度蒙版不会替代照片预览。'},
}


def read_guidance(request, observation):
    module = request.get('module')
    if module:
        names = [module]
    elif request.get('modules'):
        names = request['modules']
    else:
        fields = set(observation.get('values', {}))
        names = [name for name, members in MODULES.items() if fields.intersection(members)]
    return {
        'write_semantics': '参数patch是绝对目标值，不是相对增量；省略字段保持原值。曲线points替换完整通道，模块paste替换完整模块。',
        'limits': 'context.capabilities与adjust的校验范围决定可执行边界；说明不覆盖运行时资格或原图保护。',
        'modules': {name: deepcopy(_MODULE_GUIDANCE[name]) for name in names if name in _MODULE_GUIDANCE},
    }


MCP_INSTRUCTIONS = (
    'Capture One Windows: six client-neutral tools using one serialized local owner. '
    'Call context for the shared editing workflow, target and capabilities; read returns module guidance/units. '
    'Inspect a native preview before choosing adjustments. Edit one necessary module at a time, '
    'using absolute values and fresh observations. Suggested order: '+', '.join(ORDER)+'. '
    'Review the global result before adding local masks. Stop when the user goal is met. '
    'Stay in the selected Session; no export or document switching by default. Only registered copies are writable. '
    'Readback proves execution, not aesthetic improvement. '
    'Use an explicit unique request_id for each write so an uncertain result can be queried with operations; '
    'never repeat an uncertain write with a new ID. A client with vision/image-block support is required '
    'for visual editing decisions. Use native paths; do not silently fall back to UI/preset editing.'
)
