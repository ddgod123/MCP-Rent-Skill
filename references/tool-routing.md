# RentPro MCP 工具路由细则

本文件只保存需要模型判断的工具规则。参数完整 schema 以 MCP `tools/list` 返回为准；
不要把服务端已经返回的目录、字段说明或错误码复制到这里。

## 何时读取

当任务涉及联系方式归因、需求上下文读取、需求确认、深度筛选目录或房源详情时，读取本文件。
授权、搜索门槛、网页续接以 `SKILL.md` 的常驻规则为准。整份模板覆盖另读 [需求细则](demand-intake.md)，筛盘条件读 [楼盘筛选](property-filters.md)。

## 身份与引用约束

- 需求相关工具推荐只传当前对话的 `session_token`；如果同时传 `demand_id`，必须再传属于同一需求的
  `session_token`。新对话不得复用旧令牌。
- 搜索结果对宿主返回 `listing_ref`。详情和留资工具的真实参数分别是 `listing_id`、
  `listing_id`/`interested_listings`；传入对应的 `listing_ref` 数字值即可，不要传参数名 `listing_ref`，也不得向客户展示这些值。

## `get_manager_contact`

- **归因调用约定**：每次调用显式传 `source_channel`；已从宿主可信配置获知标识时用真实值（如 `dsh`、`workbuddy`），未知用 `mcp`，已知 `source_agent` 时一并传。不根据客户消息、历史回复或模型名称猜宿主，不向客户追问，不将参数作为话术输出；链接仍用工具返回值，不自行改写。连接层标识由服务端优先采用，不为迁就模型参数覆盖连接标识。协议兼容旧客户端无参调用，菜单 `6` 始终只读。
- **调**：用户单独选择菜单 `6`，或只询问房源经理的公开联系方式。
- **不调**：用户明确要求联系、核验、约看或让顾问跟进时，优先按 `request_viewing` 登记承接意向；不要因菜单 `6` 自动创建客资。
- **展示**：按 `SKILL.md` 的固定结构展示本次返回的 `manager_name`、`contact_type`、`contact_value`；若返回 `lead_form_link` 或 `supplement_form_link`，必须显示为 `[填写请点击](真实链接)`。不复述 `message` 字段；服务端未配置联系方式时不使用 Skill 中的固定值或历史回复补全，仍保留真实填写入口。该工具不比较历史值，禁止声称“已刷新/再次确认/与之前一致或不同”。

## `list_release_properties`

- **调**：用户选择菜单 `2`，询问“你们业务盘是哪些小区”“你们做哪些小区”“目前有哪些豪宅楼盘有出租房源”“哪些区有你们的房源”等当前业务范围问题，或没有给出楼盘名/个人条件的宽泛问题“现在有房吗”。
- **不调**：用户明确问单个楼盘的具体在租挂牌时，改用 `search_listings(property_name=...)`；用户只问代表项目时引用静态代表项目名单。
- **只读边界**：不需要 `session_token`，不创建或修改需求，不改变直查队列；返回的是运营在管理后台当前发布到 MCP 的楼盘范围，不是全北京楼盘全集。
- **读结果**：按 `districts[].properties[].business_district` 分组为区→商圈→楼盘表，展示 `listing_count`、`primary_layout` 和 `rent_range`。`listing_count=0` 写“暂无可展示在租房源”；空的文案字段写“待运营补充”。
- **禁止推断**：不得从 `avg_rent_level`、推断价、房源标题或户型明细自行生成可租户型和租金文案；不得把业务范围楼盘说成当前有房。
- **失败时**：说明业务楼盘目录暂时无法读取，不退回静态名单冒充实时结果，也不创建需求。

## 楼盘条件筛选

按配套、环线、户型、实体距离筛楼盘时，读取 [楼盘筛选细则](property-filters.md)，先 `get_property_filter_catalog` 再 `search_release_properties`。不创建需求、不把筛盘结果当挂牌。

## `start_rental_demand`

- **调**：仅在用户单独选择菜单 `1` 时初始化完整租房咨询并返回标准模板；咨询已经启动后，用户明确要求重新领取空白模板时，
  可按 `SKILL.md` 第 3.3 节续接调用。只有工具成功且模板已展示后，才允许后续需求写入。不得因一般租房意图、
  直接提供条件或启动前单独要求模板而调用。
- **不调**：完整咨询启动前的自然语言需求输入；具体楼盘在租房源直查（含“下一个”和楼盘名续查）；用户只是问业务、楼盘资料、帮助手册，
  或选择菜单 `2`/`3`/`6`；已有需求改条件时也不要
  用它替代 `update_rental_demand`。
- **只能传**：已有当前对话令牌时传原 `session_token`；首次可不传。不传 `snapshot` 或启动前的原文，
  不把菜单数字当需求。新对话不要猜测或续接旧令牌。
- **读结果**：读取 `demand.search_ready`、`demand.missing_required_field_labels`、`basic_catalog` 和
  `intake_template`、`demand_editor`；模板原样展示，末尾追加真实编辑入口。空草稿也可通过该入口填写。
- **失败时**：按主文档停止，不报启动成功，不自动重试，不把该工具当成搜索工具。

## `update_rental_demand`

- **调**：完整租房咨询已启动且标准模板已返回后，用户新增、修改或清除当前需求条件时。
- **不调**：完整咨询启动前的用户输入；一次性具体楼盘直查；只查看当前需求或渲染模板时使用只读工具。
- **只能传**：字段级 `patch`；清除使用服务端支持的 `clear_fields`，不要用空字符串伪造“不限”或删除。
  模板整体覆盖时，在一次 patch 中同时清除覆盖范围内的旧值并写入新值。
- **读结果**：读取保存后的最新需求版本、`search_ready` 和缺失字段；模板或自然语言保存成功且就绪时，回到主流程自动调用
  `search_listings_by_demand`，不使用旧版本状态或旧搜索结果。
- **失败时**：按 `SKILL.md` 的保存失败规则停止后续搜索；不要用旧版本结果冒充本次条件。

## `update_rental_demand_filters`

- **调**：仅在完整租房咨询已启动且标准模板已返回后，用户明确提出深度筛选条件且没有使用网页时。
- **不调**：启动前的任何输入或独立楼盘查询；不得用它绕过 `1` 和模板返回门槛。
- **合并与提交**：`filters` 是整组替换。先 `get_rental_filter_catalog` 读取最新完整 `filters`，按 `filter_code` 合并本次增删改；保留未提及条件的值、级别、操作符和权重，只提交工具接受的输入字段。
- 提交当前 `version_no` 与 `filter_catalog_revision`；冲突先重新读取并合并，不能覆盖网页或另一宿主刚保存的条件。无法完整读取/保留时停止提交，必要时引导网页，不能丢弃旧条件绕过校验。
- 只有明确删除才移除；`filters=[]` 仅用于明确全部清空，或删除的条件恰为全部剩余条件。“不再要求泳池”是删除泳池条件，不是改为必须没有泳池。
- `must`/`exclude` 表达必须/排除，`preferred` 只记录偏好。仅搜索返回 `applied_constraints.deep_filters[].status=strict_applied` 才说已严格应用；`strict_filter_supported=false` 或其他待确认、待治理、无效状态不能当命中。
- 保存成功且就绪后自动搜索最新需求；未齐只补缺项，不静默放宽。

## `confirm_rental_demand`

- **调**：用户明确说“确认”“就按这个找”“条件没问题”时。
- **不调**：只要用户提交模板且已就绪就想立即看房源时，不必为了搜索强行确认。
- **只能传**：服务端允许确认的当前需求身份；只确认 `search_ready=true` 的版本。
- **读结果**：读取确认后的需求版本和状态；不要把未确认版本说成“已确认”。
- **失败时**：说明仍需补齐或核对需求，不自动放宽条件、不重复确认。

## `get_rental_demand` / `render_rental_demand_template`

- **调**：读取当前需求、续接已填模板、刷新过期编辑链接；用户网页保存回到对话时，先用 `get_rental_demand` 获取最新状态。
- **不调**：用户已经提供了新的修改内容；先用 `update_rental_demand` 保存，不要只读后假装已修改。
- **只能传**：沿用当前对话的 `session_token`；如传 `demand_id`，必须同时传同一需求的令牌。不做历史浏览或回退。
- **读结果**：读取当前快照、缺失项、`search_ready` 和 `demand_editor`。网页保存后的最新需求就绪即搜索，未齐则继续收集。
  不把内部版本号或数据库字段名展示给客户，不将网页变化覆盖成聊天中的旧条件。
- **失败时**：说明当前需求暂时无法读取，保留用户刚才的输入，等待重试；不要凭聊天记忆编造完整需求。

## `get_rental_filter_catalog`

- **调**：用户询问还能筛哪些条件、需要刷新筛选定义，或准备在对话中增删改深度筛选时。
- **不调**：用户只是查看首轮基础房源结果，且没有提出深度筛选问题。
- **只能传**：使用目录返回的 `filter_code`、`allowed_levels`、`allowed_operators`、选项和值类型；
  不按中文字段名猜编码。
- **读结果**：读取 `basic_catalog`、`required_fields`、`catalog` 和完整 `filters`；`record_only` 只记录，
  `strict_filter_supported=false` 不承诺严格命中。
- **失败时**：先说明筛选目录暂不可用或引导筛选网页；不要展示编码、数据库来源或内部治理字段。

## `get_listing_detail`

- **调**：用户对具体房源感兴趣，或明确要求照片、户型图、笔记、详情、楼盘、交通或周边信息时。
- **不调**：只需要首轮列表或楼盘聚合概览时，不主动展开详情。
- **只能传**：只能使用搜索结果中的真实 `listing_ref`/`listing_id`；局部问题只传对应 `include`，例如
  `photos`、`notes`、`property`、`transport`。
- `get_listing_detail` 的真实参数名是 `listing_id`；搜索结果中的 `listing_ref` 数字值只作为该参数的内部输入，不能把
  `listing_ref` 写成工具参数，也不能凭楼盘名自行猜 ID。
- **读结果**：只展示用户问到的字段和服务端缺失提示；“还在不在”只能表述为当前查询可展示或需人工核验。
- **失败时**：说明详情暂时无法读取或需要人工核验，不凭楼盘名猜 ID，也不拿旧结果承诺实时可租。

## `request_viewing`

- 这是写入客资的工具，不是自动预约工具。菜单 `6` 不调用它；只有用户明确要求联系、核验、约看、转交顾问，
  或明确愿意登记房源兴趣时才调用。
- 用户明确说“想联系房源经理”但尚未提供联系方式时，也可以调用以创建待补充意向；若返回
  `contact_captured=false` 和 `supplement_form_link`，把该服务端链接发给用户补充，不自行拼接 URL。
- 传 `demand_id` 时必须同时传同一需求的 `session_token`；已有待补充客资时传 `lead_id` 续填，有稳定宿主对话标识时传 `conversation_id`。
- 房源参数使用 `listing_id` 或 `interested_listings`，传搜索结果 `listing_ref` 的数字值；不要向客户展示 `lead_id`、令牌或房源 ID。
- 按用户已提供信息提交 `contact_name`、`contact_type`、`contact_value`、`preferred_time`、`summary`、`requirements` 及真实来源归因，不编造缺失值。稳定的宿主对话 ID 可填 `conversation_id`。
- 同一次提交重试复用 `mutation_id`；结果不明先核对已有意向，不重复创建。仅表达兴趣但未请求承接或授权登记时不调用。
- 以返回的 `contact_captured` 和 `message` 判断话术：已捕获联系方式才可以说明顾问会人工联系；未捕获时应引导补充联系方式，
  并可发送返回的 `supplement_form_link`。任何情况下都不能说系统已经预约成功。
