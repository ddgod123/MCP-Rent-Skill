# RentPro 楼盘筛选细则

仅在按配套、环线、户型或周边距离筛楼盘时读取。先查当前目录，再提交严格支持的条件；本文件不授权建立个人需求或查询挂牌。输出另见 [楼盘结果格式](property-results.md)。

## `get_property_filter_catalog`

- **调**：用户询问“有泳池/健身房/会所的楼盘”“标准层高多少以上的楼盘”“近 N 年内交付的小区”“有地暖的小区”“二环内/四环至五环/三环沿线的楼盘”“五居及以上/大面积/450㎡以上/一梯一户/私密一些的楼盘”，或准备按物业公司、绿化率、园林等条件筛楼盘时。
- **只读边界**：不需要 `session_token`，不创建或修改需求；这是楼盘层级目录，不是挂牌筛选目录。
- **读结果**：先读取严格支持条件及其 `allowed_operators`、`allowed_values`、单位和缺失口径，再决定是否调用 `search_release_properties`。`status=pending` 必须告知当前不能严格筛选，不能转换为租房深度筛选。
- **失败时**：说明楼盘筛选目录暂不可用，不自行猜数据库字段或回退为挂牌搜索。
- **环线边界**：第一版只接受目录 `allowed_values` 中明确的区域或沿线值，并通过 `property_ring.zone` 使用；不要传原始 `ring` 字段。`三环附近`、`三环内`、`三环外` 等模糊说法先澄清，不能自行扩大或缩小范围。
- **沿线口径**：按楼盘登记点距环路 400 米的业务规则判定，350 至 450 米临界带暂待核实，不等同于楼栋或大门到道路的距离。只采用当前 `matched_facts` 或详情 `transport.ring_road`；定位、证据或几何版本变化可能让旧结果退出，不能拿历史回答补回。对客不展示几何版本、坐标或内部证据。

## `search_release_properties`

- **调**：用户明确要找满足一个或多个楼盘条件的项目，且尚未进入具体挂牌选择阶段。
- **只能传**：只传 `get_property_filter_catalog` 返回的严格条件编码；当前严格条件还包括 `property_ring.zone`（目录明确区域/沿线值）、`house_type.is_5plus_bedroom=true`、`house_type.is_300plus_sqm=true`、`house_type.bedroom_count`、`house_type.area` 和 `house_type.elevator_per_household="1梯1户"`。布尔条件使用 `eq`；标准层高和户型数值使用 `gt`/`gte`/`lt`/`lte`/`eq`；交付日期比较使用 `YYYY-MM-DD`，用户说“近 N 年内”时使用 `operator=within_years` 和整数年数；面积单位为㎡，居室单位为室。“高于/低于”分别用 `gt`/`lt`，“至少/至多”分别用 `gte`/`lte`。
- **组合语义**：同一请求中的条件默认 AND；楼盘条件在楼盘上判断，多个户型条件必须在同一真实户型上同时满足。缺失字段、面积区间或标签冲突不命中并按未知处理，不把 NULL 解释为 false。需要同时设置数值上下限时，可分别传 `gte` 和 `lte`，不要重复同一运算符。
- **读结果**：按 `districts[].properties[]` 的区→商圈→楼盘层级展示 `listing_count`、`primary_layout`、`rent_range`、`matched_facts`、`matched_house_types` 和 `property_url`；`matched_count`、`unknown_count`、`scope_count` 原样用于说明覆盖范围。`matched_house_types` 只表示存在同一户型满足全部户型条件，不把它写成整盘属性或当前有房；环线事实只使用返回的 `matched_facts`，不要展示内部关系字段；不要把 `listing_count` 当作筛选命中条件或房源详情。
- **不调**：用户已经点名某楼盘要看当前在租房源时，改用 `search_listings(property_name=...)`；用户要求按预算/居室/面积找具体挂牌时，走租房需求或挂牌搜索。客户说“私密”时不能把词直接当成“管理严格”，应明确采用一梯一户这一可核验方向。
- **失败时**：将“不支持/待治理/格式错误”的条件原样转为可理解的提示，不删除条件、不放宽条件、不改用挂牌搜索冒充楼盘筛选。

### 实体距离规则

- 周边商场、超市/生鲜、地铁条件属于楼盘筛选，不要求租房模板，不创建需求。先读取目录确认条件仍可用。
- `property_entity.mall_within_m` 只指商场/购物中心；`property_entity.supermarket_within_m` 指超市/生鲜，不含菜市场；`property_entity.metro_within_m` 只指地铁站，不泛化为交通枢纽。
- “1公里内有商场”传 `{"filter_code":"property_entity.mall_within_m","operator":"lte","value":1000}`；“不到500米”用 `lt`。只支持正数米值，最大50000米，不支持“没有商场”“距离至少X米”的反向推断。
- 距离是楼盘登记点至实体登记点的直线距离，不是大门至入口的步行路线。首次解释这一口径；用户明确要求步行时间/路程时说明暂不能严格筛选，不自行换算。仅说“附近/方便”时先澄清距离范围。
- 多类别默认 AND，各类别可以由不同实体满足。只展示返回的 `matched_facts` 并保留 `entity_notice` 的口径；同一类别的距离和实体名称必须来自同一条命中事实。筛选、楼盘页与房源详情采用同一套核验距离；详情中的 `distance_kind=straight_line` 和 `distance_notice` 延续该口径，不用缓存旧值补缺，不自行补线路或步行分钟。
- 坐标或环线重新核验期间，原有事实可能退出结果并计为未知。说明“信息待确认”，不能解释成“没有地铁/商场”，也不能凭地址或地理常识猜环线。始终使用当前工具返回值。
- 周边健身房/泳池不等于小区内部设施；医院、公园、学校、指定实体、步行时间尚未开放本批严格条件，不静默忽略客户要求或改用相近类别。未知不代表附近没有，也不代表当前有在租房源。
