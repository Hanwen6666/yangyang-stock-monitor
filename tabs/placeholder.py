"""
占位 Tab — 演示新 Tab 怎么添加

要加新 Tab:
  1. 把这个文件复制成 tabs/<your_tab>.py
  2. 实现 render(df_res, df_hist) 函数
  3. 在 tabs/__init__.py 的 TABS 列表里加一项
"""
import streamlit as st


def render(df_res, df_hist):
    st.markdown(
        """
        <div style="
            text-align:center;
            padding:80px 20px;
            color:#7a7f96;
        ">
          <div style="font-size:64px;margin-bottom:16px">🧩</div>
          <div style="font-size:18px;color:#e8eaef;font-weight:600;margin-bottom:8px">
            更多功能 · 即将上线
          </div>
          <div style="font-size:13px;line-height:1.8">
            这个 Tab 用来演示新功能怎么快速接入<br>
            需要新 Tab 时,复制 tabs/placeholder.py 改成你的业务逻辑即可
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.divider()
    st.caption("🔧 新 Tab 添加步骤")
    st.code("""
# 1. 复制占位文件
cp tabs/placeholder.py tabs/my_new_tab.py

# 2. 实现 render 函数
# def render(df_res, df_hist): ...

# 3. 在 tabs/__init__.py 注册
# from . import my_new_tab
# TABS.append({
#     "key": "my_new_tab",
#     "label": "新功能",
#     "module": my_new_tab,
#     "icon": "🆕",
# })

# 4. 推代码,Streamlit Cloud 自动重启
git add tabs/ && git commit -m "feat: 新 Tab" && git push
    """, language="bash")
