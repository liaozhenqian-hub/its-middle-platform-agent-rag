from dataclasses import dataclass, field


@dataclass(frozen=True)
class AppProfile:
    app_id: str
    display_name: str
    description: str
    domains: tuple[str, ...] = ()
    glossary: dict[str, tuple[str, ...]] = field(default_factory=dict)


class AppProfileRegistry:
    def __init__(self, profiles: list[AppProfile]):
        self._profiles = {profile.app_id: profile for profile in profiles}

    def get(self, app_id: str) -> AppProfile:
        normalized = app_id.strip()
        if normalized in self._profiles:
            return self._profiles[normalized]
        return AppProfile(
            app_id=normalized,
            display_name=normalized,
            description="企业内部应用，未配置额外领域词典。",
        )

    @classmethod
    def default(cls) -> "AppProfileRegistry":
        return cls(
            [
                AppProfile(
                    app_id="middle-platform",
                    display_name="中台",
                    description="乐歌中台，包含指标平台、审批流和工作流。",
                    domains=("指标平台", "审批流", "工作流"),
                    glossary={
                        "指标应用": ("metricType=APPLICATION", "getDataV2"),
                        "小计": ("summaryRowFlag", "summaryRow"),
                        "SDK开放接口": ("MetricClient", "/api/datacenter"),
                        "审批流": ("审批节点", "加签", "转交"),
                        "工作流": ("流程定义", "流程实例", "流程节点"),
                    },
                ),
                AppProfile(
                    app_id="erp",
                    display_name="ERP系统",
                    description="企业资源计划系统；具体内部模块和术语以后续配置为准。",
                ),
            ]
        )
