"""Query trace page — query history, Dense/Sparse comparison, and Rerank changes."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from observability.dashboard.services.trace_service import TraceService


def _render_query_list(svc: TraceService, query_filter: str) -> None:
    traces = svc.load_traces(trace_type="query", limit=50)

    if not traces:
        st.info(
            "暂无查询追踪记录。执行一次查询即可生成追踪数据。"
            "追踪数据保存在 `logs/traces.jsonl`。"
        )
        return

    # Apply query text filter
    if query_filter:
        keyword = query_filter.lower()
        traces = [
            t for t in traces
            if keyword in str(t.get("stages", [])).lower()
        ]

    st.caption(f"共找到 {len(traces)} 条记录")

    for i, trace in enumerate(traces):
        started = trace.get("started_at", 0)
        total_ms = trace.get("total_elapsed_ms", 0)
        stages = trace.get("stages", [])

        # Extract query text and key metrics
        query_text = ""
        dense_count = 0
        sparse_count = 0
        fusion_count = 0
        rerank_before = 0
        rerank_after = 0

        for s in stages:
            stage_name = s.get("stage", "")
            if stage_name == "query_rewrite":
                query_text = s.get("query", s.get("rewritten_query", ""))
            elif stage_name == "dense_search":
                dense_count = s.get("result_count", 0)
            elif stage_name == "sparse_search":
                sparse_count = s.get("result_count", 0)
            elif stage_name == "fusion":
                fusion_count = s.get("result_count", 0)
            elif stage_name == "rerank":
                rerank_before = s.get("before_count", s.get("candidate_count", 0))
                rerank_after = s.get("after_count", s.get("result_count", 0))

        display_name = query_text or trace.get("trace_id", "unknown")[:12]

        col1, col2, col3 = st.columns([3, 2, 2])
        with col1:
            st.write(f"**{display_name[:120]}**")
            st.caption(f"ID：`{trace.get('trace_id', '')[:16]}...`")
        with col2:
            st.metric("总耗时 (ms)", f"{total_ms:.1f}")
        with col3:
            st.metric("结果数", fusion_count)

        with st.expander("查询详情", expanded=(i == 0)):
            # Waterfall chart
            stage_data = svc.extract_stage_times(stages)
            if stage_data:
                df = pd.DataFrame(stage_data)
                df = df.sort_values("elapsed_ms", ascending=True)

                st.subheader("各阶段耗时分布")
                st.bar_chart(
                    df.set_index("stage")["elapsed_ms"],
                    horizontal=True,
                    use_container_width=True,
                )

                # Dense vs Sparse comparison
                st.subheader("Dense 与 Sparse 召回对比")
                compare_cols = st.columns(2)
                with compare_cols[0]:
                    st.metric("Dense 召回数", dense_count)
                with compare_cols[1]:
                    st.metric("Sparse 召回数", sparse_count)

                # Rerank change
                if rerank_before > 0:
                    st.subheader("重排序影响")
                    rerank_cols = st.columns(2)
                    with rerank_cols[0]:
                        st.metric("重排前", rerank_before)
                    with rerank_cols[1]:
                        st.metric("重排后", rerank_after)

                # Stage table
                st.subheader("阶段明细")
                for s in stage_data:
                    st.caption(
                        f"**{s['stage']}** — {s['elapsed_ms']:.1f} ms"
                        f" | 原始名：`{s['raw']}`"
                    )

            with st.expander("原始阶段数据", expanded=False):
                for s in stages:
                    filtered = {k: v for k, v in s.items() if k not in ("timestamp",)}
                    st.json(filtered)

        st.divider()


def main() -> None:
    st.title("查询追踪")
    st.caption("查看查询历史、Dense 与 Sparse 召回对比以及重排序影响。")

    svc = TraceService()

    query_filter = st.text_input(
        "搜索查询（关键词）",
        placeholder="例如：Azure OpenAI 配置…",
    )

    _render_query_list(svc, query_filter.strip() if query_filter else "")


main()
