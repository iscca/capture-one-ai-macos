# 当前执行架构

平台入口 `client/owner` 在加载时选择 Windows 或 macOS。Mac 使用 `macos_ipc → 同一 Core → MacOSBackend/MacTools → Apple Events/JXA`，单用户私有 Unix socket 串行派发；预览为用户显式启用的临时 JPEG 导出。具体限制见 [MACOS](MACOS.md)。以下记录 Windows 实现。

Windows 正常编辑通道：`CLI/MCP → Core → WindowsBackend.read_model/write_model → C1 内部管道 → UI 线程 → CoreVariant 正常 getter/setter`。

活动调整层复用这一通道：背景使用变体 getter/setter；局部使用 CoreLocalAdjustmentsLayer 的泛型 GetValue/SetValue，保留 C1 的 Undo/通知机制。先由宿主 IsPropertyAvailable 检查图层支持；白平衡使用 WhiteBalanceHelper/WhiteBalanceConverter；混色器 AddCorrection 按当前层选择属性集。读写时仍校验同一 Mask.GUID，不因允许局部调整而放宽目标检查。

候选创建调用原生 Image.CloneVariant，核对唯一新增 UUID 后调用 SelectOnly，再登记候选。主体/背景蒙版使用文档对应的 AutoMaskingManager，脱离工具面板可见性。历史其他进程/照片的未决操作不阻断新副本创建；当前实例、同一照片的未决克隆仍阻止重复创建。

模型值使用 C1 本身的 Undo/通知机制；不写 Session 数据库或侧车文件，不改进程内存地址。Basic通过原生ColorCorrection编解码写BasicColorCorrection；Advanced/Skin通过原生FromPick和CreateCCUsingMultiColorAndOtherCC构造修改，新增在setter阶段调用宿主AddCorrection，删项后修正选中索引；色轮使用原生 ColorBalanceHelper 和 ColorWheelMath；曲线直接使用 PointF[]；色阶在界面端点0～255与底层0～1之间转换，中间调−1～1，按旧端点选择安全写序；透明度写活动图层LayerOpacity。图层动作使用同一内部桥调用原生集合/命令。

启动钩子只负责连接和调度。引擎源变化后编译为新哈希的程序集，校验 SHA-256 后热加载。构建临时文件成功后才发布，拒绝不完整程序集。启动异常被捕获；仅当前用户可连接管道，令牌文件仅当前用户可读写。令牌不会出现在日志。

C1 内部同一 UI 任务核对实例 PID/启动时间、文档路径/UUID、原生照片 UUID、候选登记、原生照片路径、图层 Mask.GUID。修改旧值用精确比较；请求值先全部检查并转换，再进入正常 setter，随后在同一次UI任务中读回完整模块字段。多个 setter 不承诺数据库事务式原子回滚；中途失败明确保留未知/部分状态，不重复派发。

图层结构命令另比较旧清单。相同候选与实例的未决结构操作会阻止新的结构创建/移除，需先只读核对；历史其他实例/候选不锁整个 Session。绝对值参数请求使用当前模型前态条件。Core 只在日志层做一次未决检查，删除了之前重复的检查和自动恢复。操作记录每个 operation 保存一次前态，不再每个 action 重复整份前态。

图层重命名/开关直接设置 Name/LayerEnabled，复制调用 DuplicateLayer/Add，排序调用集合 Move。蒙版细化复用 CoreLocalAdjustmentsLayer、LocalAdjustmentsMask 和 ImgCore；线性/径向绘制保留C1的原生缓冲处理，亮度范围使用宿主内核计算后作为相交组件加入。整张蒙版复制用 CloneHandleAndCreateMaskWithMetadata，不走可能只替换当前子蒙版的菜单辅助方法。读取子蒙版时把界面的倒序转回原生索引。复制导致Mask.GUID变化时，保持同一原生图层对象和其他图层清单；不把改名后的层误判为新层。

模块动作仍使用同一个adjust入口：reset调用ResetProperties；paste使用操作记录里已有的完整模块读取快照，经现有参数编解码进入正常setter。没有新增服务、剪贴板存储或样式文件。完整回读由同一次C1调用返回，Python只转换结果，不再读一遍。蒙版回调失败也会让C1先关闭本次Undo分组，再向调用者报告失败。

选图读取当前SelectedCollection的GetAllFilteredVariants/GetAllVariants，分页使用集合UUID及有序变体UUID摘要，防止翻页期间集合内容变化。选择调用SelectedVariants.SelectOnly；列表提供仅供选择的观察凭据，允许未选中照片时直接按UUID选择。编辑仍需重新读取照片，且只允许登记副本写入。

默认预览在UI线程读取GetICProcessSettings及VariantProcessCounters，然后调用VariantProcessor.ProcessToBitmapScheduledAsync。渲染和PNG编码在后台线程完成，轮询只读任务结果；每次比对实例、文档、照片、图层、尺寸及调整计数。蒙版使用相同渲染器与mdtGreyscale，返回不透明灰度图。坐标转换使用VariantGeometryTransformer，不用近似旋转或截图定位。只缓存最近一个任务及最近8张PNG，MCP直接返回PNG，没有JPEG代理的重复编码；旧PrintWindow截图和UIA导出只有显式调用时使用。

此内部接口依赖当前 C1 程序集，验证版本为 16.8.6.3784；不等同官方公共 SDK。Basic／Advanced／Skin、色阶四通道、色轮四轮、曲线五通道均已在副本写入回读；此前原生图层／蒙版证据保留。更多版本与本轮排除模块不继承资格。见 [验证范围](SUPPORT.md)。
