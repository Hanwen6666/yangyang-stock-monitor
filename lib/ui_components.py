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

    Step2: 极端档位(超强势/一直下跌)数字加 pulse 动画给视觉锚点。
    """
    sub_field = sub_html if sub_html is not None else escape(sub)
    # 脉冲动画: 仅限极端档位（超强势=暖红/一直下跌=冷灰, 颜色识别）
    pulse_class = ""
    pulse_style = ""
    # ACCENT_UP=#ff4d4f 红 / ACCENT_DN=#22c55e 绿 / 一直下跌 glow=#6b7894 灰
    color_lower = escape(color).lower()
    if color_lower in ("#ff4757", "#ff1a3d", "#ff4d4f") or "255,71,87" in color_lower or color_lower == "#ff4757":
        pulse_class = "kpi-pulse-up"
        pulse_style = "animation: kpi-pulse-up 2.4s ease-in-out infinite;"
    elif color_lower in ("#2a3450", "#6b7894", "#1c2538") or "42,52,80" in color_lower:
        pulse_class = "kpi-pulse-down"
        pulse_style = "animation: kpi-pulse-down 2.4s ease-in-out infinite;"
    value_class = f'kpi-value {pulse_class}'.strip()
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
        f'<div class="{value_class}" style="color:{escape(color)};font-size:{FONT_KPI_VALUE}px;font-weight:700;'
        f'font-family:monospace;margin-top:2px;line-height:1.15;'
        f'font-feature-settings:&quot;tnum&quot;;{pulse_style}">{escape(value)}</div>'
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


# ============================================================
# 2026-07-20 重构增量: 安全 HTML utility (统一 escape 入口)
# 背景: 全栈多出 st.markdown(unsafe_allow_html=True) 调用, escape() 调用不一致,
#       XSS 风险统一在 safe_html() 入口处理
# ============================================================

def safe_html(tag: str, content: str = "", **attrs: str) -> str:
    """统一安全 HTML 生成器: 自动 escape 所有属性值和内容

    用法:
        safe_html("td", "510300", color=TEXT, style="padding:5px 8px;")
        safe_html("span", label_badge_html(value))

    Args:
        tag: HTML 标签名 (如 "td" / "span" / "div")
        content: 标签内部文本 (会自动 escape)
        **attrs: HTML 属性 (会自动 escape 属性值)
                常用: style, class_, id, color

    Returns:
        完整 HTML 字符串

    Note:
        - class 是 Python 保留字, 使用 class_ 代替
        - style 字符串默认 escape; 如果 style 必须是 CSS 颜色 {TEXT_MUTED} 之类
          Python f-string 插值的常量, 在调用方拼接后传入 (不需 escape)
    """
    attrs_html = ""
    for k, v in attrs.items():
        if v is None:
            continue
        # class → class_ (Python 保留字)
        attr_name = "class" if k == "class_" else k.replace("_", "-")
        attrs_html += f' {attr_name}="{escape(str(v))}"'
    return f'<{tag}{attrs_html}>{escape(str(content))}</{tag}>'


def td_html(content: str, color: str = TEXT, *, mono: bool = False,
            bold: bool = False, align: str = "left") -> str:
    """统一表格数据单元格 — 替代 <td style="padding:5px 8px;color:X;font-family:monospace;text-align:right;">...

    Args:
        content: 单元格文本 (自动 escape)
        color: 文本颜色 (来自 lib.constants, 例如 TEXT / TEXT_MUTED / ACCENT_UP)
        mono: True → font-family:monospace (用于数字/代码)
        bold: True → font-weight:700
        align: "left" / "center" / "right"

    Returns:
        <td> 完整 HTML (已 escape content)
    """
    style_parts = [f"padding:5px 8px", f"color:{color}", f"text-align:{align}"]
    if mono:
        style_parts.append("font-family:monospace")
    if bold:
        style_parts.append("font-weight:700")
    style = ";".join(style_parts) + ";"
    return safe_html("td", content, style=style)


def th_html(content: str, color: str = TEXT_MUTED, align: str = "left") -> str:
    """统一表格表头单元格 — 替代 <th style="padding:6px 8px;text-align:left;color:X;font-size:11px;">...

    Args:
        content: 表头文本 (自动 escape)
        color: 文本颜色 (默认 TEXT_MUTED)
        align: "left" / "center" / "right"

    Returns:
        <th> 完整 HTML (已 escape content)
    """
    style = f"padding:6px 8px;text-align:{align};color:{color};font-size:11px;"
    return safe_html("th", content, style=style)