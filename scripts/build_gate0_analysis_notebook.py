#!/usr/bin/env python3
import ast
import contextlib
import io
import json
import os
from pathlib import Path


class NotebookV4:
    @staticmethod
    def new_notebook(metadata):
        return {"cells": [], "metadata": metadata, "nbformat": 4, "nbformat_minor": 5}

    @staticmethod
    def new_markdown_cell(source):
        return {"cell_type": "markdown", "metadata": {}, "source": source}

    @staticmethod
    def new_code_cell(source):
        return {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": source,
        }


class NotebookFormat:
    v4 = NotebookV4()

    @staticmethod
    def write(notebook, path):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        temporary.replace(path)


class NotebookClient:
    def __init__(self, notebook, timeout=None, kernel_name=None):
        self.notebook = notebook

    def execute(self, cwd):
        namespace = {"__name__": "__gate0_notebook__"}
        previous = Path.cwd()
        os.chdir(cwd)
        try:
            execution_count = 0
            for cell in self.notebook["cells"]:
                if cell["cell_type"] != "code":
                    continue
                execution_count += 1
                tree = ast.parse(cell["source"], filename=str(NOTEBOOK_PATH))
                final_expression = tree.body[-1] if tree.body and isinstance(tree.body[-1], ast.Expr) else None
                body = tree.body[:-1] if final_expression else tree.body
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    if body:
                        module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
                        exec(compile(module, str(NOTEBOOK_PATH), "exec"), namespace)
                    value = None
                    if final_expression:
                        expression = ast.fix_missing_locations(ast.Expression(final_expression.value))
                        value = eval(compile(expression, str(NOTEBOOK_PATH), "eval"), namespace)
                cell["execution_count"] = execution_count
                cell["outputs"] = []
                if output.getvalue():
                    cell["outputs"].append(
                        {"name": "stdout", "output_type": "stream", "text": output.getvalue()}
                    )
                if value is not None:
                    cell["outputs"].append(
                        {
                            "data": {"text/plain": repr(value)},
                            "execution_count": execution_count,
                            "metadata": {},
                            "output_type": "execute_result",
                        }
                    )
        finally:
            os.chdir(previous)
        return self.notebook


nbformat = NotebookFormat()


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = PROJECT_ROOT / "reports" / "gate0-data-preflight"
NOTEBOOK_PATH = REPORT_ROOT / "gate0-analysis.ipynb"


def main():
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    notebook = nbformat.v4.new_notebook(
        metadata={
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3"},
        }
    )
    notebook["cells"] = [
        nbformat.v4.new_markdown_cell(
            "# Gate 0 数据预检可复算分析\n\n"
            "本笔记读取最新影子预检与可选的90天回扫结果，验证来源覆盖、失败分类和硬门槛缺口。"
            "它不修改 C2.0、生产数据库或既有调度。"
        ),
        nbformat.v4.new_code_cell(
            "from collections import Counter\n"
            "from pathlib import Path\n"
            "import json\n"
            "project_root = Path.cwd().resolve().parents[1]\n"
            "run_path = project_root / 'runtime' / 'gate0-shadow' / 'latest.json'\n"
            "manifest_path = project_root / 'runtime' / 'gate0-shadow' / 'manifest.jsonl'\n"
            "scope_path = project_root / 'config' / 'gate0-shadow-scope.json'\n"
            "backfill_path = project_root / 'runtime' / 'gate0-shadow' / 'backfill' / 'coverage-rollup.json'\n"
            "run = json.loads(run_path.read_text(encoding='utf-8'))\n"
            "manifest = [json.loads(line) for line in manifest_path.read_text(encoding='utf-8').splitlines() if line.strip()]\n"
            "scope = json.loads(scope_path.read_text(encoding='utf-8'))\n"
            "backfill = json.loads(backfill_path.read_text(encoding='utf-8')) if backfill_path.exists() else None\n"
            "run['runId'], run['finishedAt']"
        ),
        nbformat.v4.new_markdown_cell("## 六链接入范围与公开分页边界"),
        nbformat.v4.new_code_cell(
            "coverage = [{key: row.get(key) for key in (\n"
            "    'networkId','state','pagesSucceeded','poolsCollected','oldestPoolCreatedAt','stopReason','coversNinetyDays'\n"
            ")} for row in run['discoveryCoverage']]\n"
            "coverage"
        ),
        nbformat.v4.new_markdown_cell("## 来源状态与硬门槛缺口"),
        nbformat.v4.new_code_cell(
            "requests = run['requests']\n"
            "request_states = [\n"
            "    {'source': source, 'state': state, 'requests': count}\n"
            "    for (source, state), count in sorted(Counter(\n"
            "        (row['source'], row['state']) for row in requests\n"
            "    ).items())\n"
            "]\n"
            "blocking = sorted([\n"
            "    {'reason': reason, 'candidates': count}\n"
            "    for reason, count in run['preGateBlockingReasons'].items()\n"
            "], key=lambda row: row['candidates'], reverse=True)\n"
            "request_states, blocking"
        ),
        nbformat.v4.new_markdown_cell("## Gate 0 通过条件审计"),
        nbformat.v4.new_code_cell(
            "shadow_days = sorted({\n"
            "    row['finishedAt'][:10]\n"
            "    for row in manifest\n"
            "    if row.get('finishedAt') and row.get('usableForShadowDay') is True\n"
            "})\n"
            "db = run['databaseProfile']\n"
            "checks = {\n"
            "    'database_read_only': db['openMode'] == 'read_only',\n"
            "    'database_integrity_ok': db['integrityCheck'] == 'ok' and db['foreignKeyErrors'] == 0,\n"
            "    'all_requests_classified': all(row.get('state') for row in requests),\n"
            "    'ninety_day_initial_backfill_available': bool(backfill) and backfill['coverage']['historicalBackfillComplete'],\n"
            "    'helius_usage_observable': any(\n"
            "        row.get('source') == 'helius_usage' and row.get('state') == 'success'\n"
            "        for row in run['capabilityProbes']\n"
            "    ),\n"
            "}\n"
            "assert checks['database_read_only'] and checks['database_integrity_ok']\n"
            "assert checks['all_requests_classified']\n"
            "[{'check': key, 'passed': value} for key, value in checks.items()]"
        ),
        nbformat.v4.new_code_cell(
            "summary = {\n"
            "    'schemaVersion': 'convexity-gate0-analysis-v0.1',\n"
            "    'runId': run['runId'],\n"
            "    'finishedAt': run['finishedAt'],\n"
            "    'shadowDaysObserved': len(shadow_days),\n"
            "    'liveReliabilityDaysObserved': len(shadow_days),\n"
            "    'liveReliabilityTargetDistinctDays': scope['boundary']['liveReliabilityTargetDistinctDays'],\n"
            "    'liveReliabilityBlocksBackfillOrDevelopment': scope['boundary']['liveReliabilityBlocksBackfillOrDevelopment'],\n"
            "    'gate0Passed': all(checks.values()),\n"
            "    'checks': checks,\n"
            "    'counts': run['counts'],\n"
            "    'requestSummary': run['requestSummary'],\n"
            "    'securityOutcomes': run['securityOutcomes'],\n"
            "    'coverage': run['discoveryCoverage'],\n"
            "    'blockingReasons': run['preGateBlockingReasons'],\n"
            "    'capabilityProbes': run['capabilityProbes'],\n"
            "    'blockers': run['blockers'],\n"
            "    'backfill': backfill,\n"
            "}\n"
            "output = Path.cwd() / 'analysis-summary.json'\n"
            "output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')\n"
            "summary"
        ),
        nbformat.v4.new_markdown_cell(
            "## 解释边界\n\n"
            "- `poolsCollected` 是公开接口在本次分页范围内返回的池，不是全市场项目数。\n"
            "- `no_data` 表示来源成功但未返回该对象；它不等于项目质量差。\n"
            "- 平台附带网站或 GitHub 链接在独立映射前只是待核验证据。\n"
            "- 所有项目只使用接口返回的真实历史数据；项目无需等待任何额外观察日。\n"
            "- 14个不同自然日只用于非阻塞的实时采集稳定性记录，不阻塞历史回扫、开发或冻结判断。\n"
            "- 90天回扫、免费额度和项目—合约映射是独立能力检查，不能互相替代。"
        ),
    ]
    nbformat.write(notebook, NOTEBOOK_PATH)
    client = NotebookClient(notebook, timeout=180, kernel_name="python3")
    client.execute(cwd=str(REPORT_ROOT))
    nbformat.write(notebook, NOTEBOOK_PATH)
    print(NOTEBOOK_PATH)


if __name__ == "__main__":
    main()
