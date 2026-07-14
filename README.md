## 3. B2：Skill 独立演示

入口：`code/b2_run_skill.py`

### 3.1 通用命令行输入

| 参数 | 说明 |
|---|---|
| `--skill` | Skill 名称：五个基础 Skill，或 `list_directory`、`python_executor`、`web_fetcher`、`read_and_convert`。 |
| `--input` | 对应 Skill 的 JSON 输入文件。顶层必须是 JSON 对象。 |
| `--outdir` | B2 输出目录。 |
| `--data_root` | 可选的数据根目录；未提供时使用项目的 `data/`。 |
| `--tools_config` | 可选；注入离线模型路径、域名白名单和资源上限，并执行功能开关。 |

### 3.2 每个 Skill 的输入文件

| Skill | 正常输入文件 | 关键输入字段 | 异常输入样例 |
|---|---|---|---|
| calculator | `data/tool_inputs/tool_input_calculator.json` | `expression`：数学表达式字符串。 | `tool_input_calculator_error.json` |
| file_reader | `data/tool_inputs/tool_input_file_reader.json` | `path`：相对 `data/` 的 txt/md 路径；`max_chars`：最大返回字符数。 | `tool_input_file_reader_error.json` |
| local_file_search | `data/tool_inputs/tool_input_file_search.json` | `query`、`root_dir`、`file_types`、`top_k`。 | `tool_input_file_search_error.json` |
| table_analyzer | `data/tool_inputs/tool_input_table_analyzer.json` | `path`：CSV/TSV 路径；`max_rows_preview`；`describe`。 | `tool_input_table_analyzer_error.json` |
| format_converter | `data/tool_inputs/tool_input_format_converter.json` | `text`；`target_format`：`markdown` 或 `json`；可选 `output_filename`。 | `tool_input_format_converter_error.json` |

文件类 Skill 的相对路径以 `data/` 为根。例如 `docs/agent_intro.txt` 实际对应 `data/docs/agent_intro.txt`。

### 3.3 演示命令

```bash
python b2_run_skill.py --skill calculator --input ../data/tool_inputs/tool_input_calculator.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill calculator --input ../data/tool_inputs/tool_input_calculator_error.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill file_reader --input ../data/tool_inputs/tool_input_file_reader.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill file_reader --input ../data/tool_inputs/tool_input_file_reader_error.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill local_file_search --input ../data/tool_inputs/tool_input_file_search.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill local_file_search --input ../data/tool_inputs/tool_input_file_search_error.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill table_analyzer --input ../data/tool_inputs/tool_input_table_analyzer.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill table_analyzer --input ../data/tool_inputs/tool_input_table_analyzer_error.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill format_converter --input ../data/tool_inputs/tool_input_format_converter.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill format_converter --input ../data/tool_inputs/tool_input_format_converter_error.json --outdir ../outputs/B2_skills
python b2_run_skill.py --skill list_directory --input ../data/tool_inputs/tool_input_list_directory.json --tools_config ../configs/tools.yaml --outdir ../outputs/B2_skills
python b2_run_skill.py --skill list_directory --input ../data/tool_inputs/tool_input_list_directory_error.json --tools_config ../configs/tools.yaml --outdir ../outputs/B2_skills
python b2_run_skill.py --skill python_executor --input ../data/tool_inputs/tool_input_python_executor.json --tools_config ../configs/tools.yaml --outdir ../outputs/B2_skills
python b2_run_skill.py --skill python_executor --input ../data/tool_inputs/tool_input_python_executor_error.json --tools_config ../configs/tools.yaml --outdir ../outputs/B2_skills
python b2_run_skill.py --skill read_and_convert --input ../data/tool_inputs/tool_input_read_and_convert.json --tools_config ../configs/tools.yaml --outdir ../outputs/B2_skills
python b2_run_skill.py --skill read_and_convert --input ../data/tool_inputs/tool_input_read_and_convert_error.json --tools_config ../configs/tools.yaml --outdir ../outputs/B2_skills

