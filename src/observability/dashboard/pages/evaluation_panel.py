"""Evaluation panel page — run evaluation, view metrics, and compare results (H4)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from core.settings import REPO_ROOT
from libs.evaluator.evaluator_factory import EvaluatorFactory
from libs.evaluator.base_evaluator import EvaluatorSettings

DEFAULT_TEST_SET = REPO_ROOT / "tests" / "fixtures" / "golden_test_set.json"

# 指标键名 → 中文显示名（键名来自评估结果数据，仅用于展示）
_METRIC_LABELS = {
    "hit_rate": "命中率 (hit_rate)",
    "mrr": "平均倒数排名 (mrr)",
    "precision": "精确率 (precision)",
    "recall": "召回率 (recall)",
    "faithfulness": "忠实度 (faithfulness)",
    "answer_relevancy": "答案相关性 (answer_relevancy)",
    "context_precision": "上下文精确率 (context_precision)",
    "context_recall": "上下文召回率 (context_recall)",
}


def _metric_label(key: str) -> str:
    return _METRIC_LABELS.get(key, key)


def _load_test_set(path: str) -> int:
    """Count test cases in a golden test set file."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return len(data.get("test_cases", []))
    except Exception:
        return 0


def _load_report(json_path: str) -> dict | None:
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def main() -> None:
    st.title("评估面板")
    st.caption("针对黄金测试集运行评估并查看指标。")

    providers = EvaluatorFactory.list_providers()

    col1, col2, col3 = st.columns(3)
    with col1:
        provider = st.selectbox("评估器", providers, index=0)
    with col2:
        test_set_default = str(DEFAULT_TEST_SET) if DEFAULT_TEST_SET.exists() else ""
        test_set_path = st.text_input(
            "黄金测试集路径",
            value=test_set_default,
            help="含 test_cases 字段的 JSON 文件路径",
        )
    with col3:
        top_k = st.number_input("保留条数 Top-K", min_value=1, value=10, max_value=100)

    if test_set_path:
        count = _load_test_set(test_set_path)
        st.caption(f"测试集包含 **{count}** 条查询。")

    if st.button("开始评估", type="primary", use_container_width=True):
        if not test_set_path or not os.path.isfile(test_set_path):
            st.error(f"测试集不存在：`{test_set_path}`")
            return

        with st.spinner("正在评估…"):
            try:
                from observability.evaluation.eval_runner import EvalRunner

                eval_settings = EvaluatorSettings(provider=provider, top_k=top_k)
                evaluator = EvaluatorFactory.create(eval_settings)

                runner = EvalRunner(evaluator=evaluator)
                report = runner.run(test_set_path)

                st.success(f"评估完成 — 共 {report.total_queries} 条查询")

                # Aggregate metrics
                st.subheader("汇总指标")
                metric_cols = st.columns(4)
                metric_keys = sorted(report.metrics.keys())
                for i, k in enumerate(metric_keys):
                    with metric_cols[i % 4]:
                        st.metric(_metric_label(k), f"{report.metrics[k]:.4f}")

                # Per-query details
                st.subheader("逐条查询结果")
                if report.per_query:
                    rows = []
                    for qr in report.per_query:
                        row = {
                            "查询": str(qr.get("query", ""))[:80],
                            "召回条数": len(qr.get("retrieved_ids", [])),
                            "期望条数": len(qr.get("expected_ids", [])),
                        }
                        row.update(
                            {_metric_label(k): v for k, v in (qr.get("metrics") or {}).items()}
                        )
                        rows.append(row)

                    if rows:
                        st.dataframe(
                            pd.DataFrame(rows),
                            use_container_width=True,
                            hide_index=True,
                        )

                # Detailed breakdown
                with st.expander("完整报告 JSON", expanded=False):
                    st.json(report.to_dict())

            except Exception as exc:
                st.error(f"评估失败：{exc}")

    st.divider()
    st.subheader("历史报告")

    reports_dir = REPO_ROOT / "logs" / "eval_reports"
    if reports_dir.exists():
        report_files = sorted(
            reports_dir.glob("*.json"),
            key=os.path.getmtime,
            reverse=True,
        )[:5]
        if report_files:
            for rf in report_files:
                report = _load_report(str(rf))
                if report:
                    with st.expander(
                        f"{rf.name} — {report.get('total_queries', '?')} 条查询"
                    ):
                        st.json(report, expanded=False)
        else:
            st.caption("暂无历史报告。")
    else:
        st.caption(
            "暂无历史报告。执行一次评估即可在 "
            f"`{reports_dir}` 生成。"
        )


main()
