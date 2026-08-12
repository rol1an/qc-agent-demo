"""
影子协同层 —— 【分级放权阶梯】: AI 的自主执行权不是天生的，是用数据挣来的。

比"检测后提醒人处理"多走一步的核心机制:
  影子期(shadow) : 即使 gate 判 A(证据齐、置信够)，AI 也只出【建议】，处置仍由人
                   执行；系统同时记录 AI 建议与产线实际走向的一致性(数据回放中，
                   人工处置后的复测结果即实际走向)。
  自主期(auto)   : 某类处置在影子期攒够样本(n≥N_MIN)且一致率达标(≥PROMOTE_AT)后，
                   该类处置获得自主执行权 → gate 判 A 时 AI 直接执行+强制复测。
  降级(demote)   : 自主执行后复测未恢复 → 立即收回该类处置的自主权，退回影子期。

设计原则(与三态 gate 同源): 放权/收权由确定性代码按可复核的统计规则裁决，
不由模型自评。阈值是【放权门槛参数】而非效果承诺——demo 用小样本门槛跑通机制，
生产环境按赛力斯质量体系配置(如 n≥200、一致率≥99%)。
"""
from __future__ import annotations
from dataclasses import dataclass, field

N_MIN = 3            # 放权最小样本量(demo 值；生产建议 ≥200)
PROMOTE_AT = 1.0     # 放权一致率门槛(demo 小样本取 100%；生产建议 ≥0.99)


@dataclass
class DispositionTrack:
    level: str = "shadow"        # 'shadow' | 'auto'
    n: int = 0                   # 影子期累计观察数
    agree: int = 0               # AI 建议与实际走向一致数
    promoted_at: int | None = None   # 获得自主权时的批次号
    demotions: int = 0           # 历史降级次数

    @property
    def agree_rate(self) -> float:
        return self.agree / self.n if self.n else 0.0


@dataclass
class AutonomyLedger:
    """按处置类型记账的放权台账 —— 全部状态可打印、可审计。"""
    tracks: dict[str, DispositionTrack] = field(default_factory=dict)

    def track(self, disposition: str) -> DispositionTrack:
        return self.tracks.setdefault(disposition, DispositionTrack())

    def is_autonomous(self, disposition: str) -> bool:
        return self.track(disposition).level == "auto"

    def observe_shadow(self, disposition: str, agreed: bool, batch_idx: int) -> str | None:
        """影子期记一笔观察；达到放权条件则晋升，返回描述(未晋升返回 None)。"""
        t = self.track(disposition)
        t.n += 1
        t.agree += int(agreed)
        if t.level == "shadow" and t.n >= N_MIN and t.agree_rate >= PROMOTE_AT:
            t.level = "auto"
            t.promoted_at = batch_idx
            return (f"『{disposition}』影子期 {t.n} 例一致率 {t.agree_rate:.0%} ≥ "
                    f"{PROMOTE_AT:.0%}(n≥{N_MIN}) → 获得自主执行权")
        return None

    def demote(self, disposition: str) -> str:
        """自主执行复测未恢复 → 立即收权退回影子期(一致率清零重攒)。"""
        t = self.track(disposition)
        t.level, t.n, t.agree, t.demotions = "shadow", 0, 0, t.demotions + 1
        return f"『{disposition}』自主执行复测未恢复 → 收回自主权，退回影子期重新累积"

    def summary(self) -> list[str]:
        """人读的台账。降级过的条目要说清"现在是影子期"，不能只写"某批次起放权"。"""
        out = []
        for d, t in self.tracks.items():
            head = f"『{d}』[{'自主期' if t.level == 'auto' else '影子期'}] 观察{t.n}例 一致率{t.agree_rate:.0%}"
            if t.level == "auto":
                hist = f" (批次#{t.promoted_at}起放权)" if t.promoted_at is not None else ""
                if t.demotions:
                    hist += f"，历史降级{t.demotions}次后重新挣回"
            elif t.promoted_at is not None:
                hist = (f" (曾于批次#{t.promoted_at}放权，复测未恢复已收权"
                        + (f"{t.demotions}次" if t.demotions > 1 else "") + "，正重新累积)")
            else:
                hist = " (尚未获得自主权)"
            out.append(head + hist)
        return out
