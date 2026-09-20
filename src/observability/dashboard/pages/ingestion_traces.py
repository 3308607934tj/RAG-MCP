"""Ingestion trace page — ingestion history list with stage waterfall charts."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from observability.dashboard.services.trace_service import TraceService


def _render_trace_list(svc: TraceService) -> None:
    traces = svc.load_traces(trace_type="ingestion", limit=50)

    if not traces:
        st.info(
            "暂无摄取追踪记录。执行一次摄取即可生成追踪数据。"
            "追踪数据保存在 `logs/traces.jsonl`。"
        )
        return

    st.caption(f"共找到 {len(traces)} 条记录")

    for i, trace in enumerate(traces):
        started = trace.get("started_at", 0)
        finished = trace.get("finished_at")
        total_ms = trace.get("total_elapsed_ms", 0)
        file_path = ""
        file_hash = ""
        stages = trace.get("stages", [])

        for s in stages:
            if s.get("stage") == "integrity":
                file_path = s.get("file_path", "")
                file_hash = s.get("file_hash", "")
                break

        display_name = file_path or trace.get("trace_id", "unknown")[:12]

        col1, col2, col3 = st.columns([3, 2, 2])
        with col1:
            st.write(f"**{display_name}**")
            if file_hash:
                st.caption(f"哈希：`{file_hash[:16]}...`")
        with col2:
            st.metric("总耗时 (ms)", f"{total_ms:.1f}")
        with col3:
            chunk_count = 0
            for s in stages:
                if s.get("stage") == "pipeline_done":
                    chunk_count = s.get("chunk_count", 0)
                    break
            st.metric("分块数", chunk_count)

        with st.expander("阶段详情", expanded=(i == 0)):
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

                st.subheader("阶段明细")
                for s in stage_data:
                    st.caption(
                        f"**{s['stage']}** — {s['elapsed_ms']:.1f} ms"
                        f" | 原始名：`{s['raw']}`"
                    )

            # Also show raw stage details
            with st.expander("原始阶段数据", expanded=False):
                for s in stages:
                    filtered = {k: v for k, v in s.items() if k not in ("timestamp",)}
                    st.json(filtered)

        st.divider()


def main() -> None:
    st.title("摄取追踪")
    st.caption("查看摄取历史、各阶段耗时与性能分析。")

    svc = TraceService()
    _render_trace_list(svc)


main()
