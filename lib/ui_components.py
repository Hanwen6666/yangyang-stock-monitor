"""
UI 组件 — Streamlit 用的 HTML 渲染辅助

所有用到 unsafe_allow_html=True 的 HTML 拼接都集中在这里,统一:
  - escape 用户输入(防御 XSS)
  - 主题色/字号走 lib.constants 的常量
"""
from html import escape

from lib.constants import (
    BG_PANEL, BORDER, BORDER_HI, TEXT, TEXT_MUTED, TEXT_DIM,
    LABEL_STYLES,
    FONT_KPI_TITLE, FONT_KPI_VALUE, FONT_KPI_SUB,
    FONT_METRIC_TITLE, FONT_METRIC_VALUE,
    KPI_CARD_HEIGHT,
)


def label_badge_html(label: str) -> str:
    """6档趋势标签 — 渐变底+圆角+微光晕(大厂质感)"""
    s = LABEL_STYLES.get(label)
    if s:
        return (
            f'<span style="'
            f'background:{s["gradient"]};color:{s["fg"]};'
            f'padding:3px 10px;border-radius:6px;'
            f'font-size:11px;font-weight:600;letter-spacing:0.3px;'
            f'display:inline-block;white-space:nowrap;'
            f'box-shadow:inset 0 1px 0 rgba(255,255,255,0.15),0 1px 3px rgba(0,0,0,0.3);'
            f'">{escape(label)}</span>'
        )
    s = LABEL_STYLES.get(label)
    bg, fg = (s["bg"], s["fg"]) if s else ("#3a4156", "#fff")
    return (
        f'<span style="'
        f'background:{bg};color:{fg};'
        f'padding:3px 10px;border-radius:6px;'
        f'font-size:11px;font-weight:600;letter-spacing:0.3px;'
        f'display:inline-block;white-space:nowrap;'
        f'">{escape(label)}</span>'
    )


def kpi_card(title: str, value: str, sub: str, color: str, hover_color: str | None = None,
             sub_html: str | None = None) -> str:
    """统一 KPI 卡(紧凑版,高度固定 KPI_CARD_HEIGHT)

    sub 会自动 escape;如果需要富文本(例如包含额外的 span 注释),传
    sub_html 参数则原样插入(不接受用户原始输入,调用方负责 XSS)。
    color 也仅会 escape,作为颜色字符串使用。
    """
    sub_field = sub_html if sub_html is not None else escape(sub)
    return (
        f'<div class="kpi-card" '
        f'style="background:{BG_PANEL};border:1px solid {BORDER};'
        f'border-radius:8px;padding:8px 10px;height:{KPI_CARD_HEIGHT};'
        f'position:relative;overflow:hidden;'
        f'transition:all 0.15s ease;cursor:default;">'
        # 顶部 2px 色带
        f'<div style="position:absolute;top:0;left:0;right:0;height:2px;'
        f'background:linear-gradient(90deg,{escape(color)},{escape(color)}88);'
        f'border-radius:8px 8px 0 0;"></div>'
        f'<div style="color:{TEXT_MUTED};font-size:{FONT_KPI_TITLE}px;font-weight:500;'
        f'letter-spacing:0.5px;text-transform:uppercase;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
        f'margin-top:2px;">{escape(title)}</div>'
        f'<div class="kpi-value" style="color:{escape(color)};font-size:{FONT_KPI_VALUE}px;font-weight:700;'
        f'font-family:monospace;margin-top:2px;line-height:1.15;'
        f'font-feature-settings:&quot;tnum&quot;;">{escape(value)}</div>'
        f'<div style="color:{TEXT_DIM};font-size:{FONT_KPI_SUB}px;margin-top:1px;'
        f'font-family:monospace;">{sub_field}</div>'
        '</div>'
    )


def metric_row_html(metrics_list) -> str:
    """东财风格行内指标条(单行HTML,避免Streamlit多行显示异常)

    metrics_list: [(title, value, color), ...]
    """
    items = ""
    for title, value, color in metrics_list:
        items += (
            '<div style="text-align:center;flex:1;min-width:0;'
            'border-right:1px solid #1f2638;padding:0 8px;">'
            f'<div style="color:{TEXT_DIM};font-size:{FONT_METRIC_TITLE}px;'
            'text-transform:uppercase;letter-spacing:0.5px;'
            f'margin-bottom:2px;">{escape(title)}</div>'
            f'<div style="color:{escape(color)};font-size:{FONT_METRIC_VALUE}px;font-weight:700;'
            f'font-family:monospace;">{escape(value)}</div>'
            '</div>'
        )
    return (
        f'<div style="display:flex;background:{BG_PANEL};border:1px solid {BORDER};'
        f'border-radius:6px;padding:6px 0;margin-bottom:4px;">'
        + items + '</div>'
    )


# 暴露给旧调用方的别名(向后兼容)
_metric_row_html = metric_row_html