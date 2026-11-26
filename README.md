# 长桥合唱排练谱服务

谱务员载入分声部词谱，指挥拆句试读并恢复句界，领唱员使用手中编号谱标出呼吸，谱务员在收场时复查每个声部的实际领用记录。服务使用 Python 3.11 标准库、本机 JSON HTTP 和一个 SQLite 库。

## 运行

在项目根目录运行。

```sh
python3 -B check_project.py
python3 -B -m examples.demo
python3 -B -m rehearsal.server --db rehearsal.sqlite3 serve --port 8000
```

第一次启动建立空库。服务默认绑定 `127.0.0.1`。重复初始化保留既有稿件、修订、发行、领用和收场记录。样例由谱务员显式导入。

```sh
curl -sS -X POST -H 'Content-Type: application/json' --data-binary @examples/draft.json http://127.0.0.1:8000/load
python3 -B -m rehearsal.server --db rehearsal.sqlite3 prepare-run --run morning --draft bridge
```

`prepare-run` 采用该稿的声部建立开放轮次，返回轮次、所属稿件、闭场状态及参与声部的当前用谱。同号同稿现有轮次返回原状态，同号异稿为冲突。编号均由各自入口返回，后续操作请使用实际返回值。

## JSON HTTP

POST 使用 UTF-8 JSON 对象及 `Content-Type: application/json`，GET 使用查询参数。成功响应为 200，业务错误返回 `{"error":"错误码"}`。400 表示无效输入，404 表示稿件、修订、谱、声部、轮次或操作缺失，409 表示重复稿号、过期父修订、不可撤销操作、同号异参、错稿领用或轮次闭场。存储失败为 500。一次写请求的业务变更在同一事务提交。

| 调用 | 输入 | 成功结果 |
| --- | --- | --- |
| `POST /load` | `draft` 与 `voices`。每个声部有 `voice`、`notes`，每个音符有 `beat`、`lyric` | 稿号、初始 `revision` 和声部列表 |
| `POST /edit` | `draft`、`parent`、`kind`。`split` 另需 `phrase`、`position`，`undo` 另需 `operation` | 新 `revision` 和本次 `operation` |
| `POST /issue` | `draft`、`revision` | 固定 `issue`、所属稿和修订，以及全部 `sheets` |
| `GET /read` | `voice` 加 `draft` 或 `issue` 二者之一 | 该声部的 `phrases`、稿号、修订、谱号。工作谱的 `issue` 为 `null` |
| `POST /mark` | `request`、`issue`、`voice`、`phrase`、`position`、`text` | 首次受理的 `mark_id` 及原提交参数。同号同参复领相同结果 |
| `POST /handout` | `run`、`voice`、`issue` | `handout_id`、轮次、声部和本次实领谱号 |
| `POST /close` | `run` | 稿、轮次及按实领顺序保全的 `handouts` |

稿号、轮次号、声部、句号、请求号和标记正文使用非空字符串。修订号、操作号、谱号是正整数。拍位使用 JSON 整数或正分母的有理数字符串，例如 `1`、`"3/2"`、`2`。按精确拍值比较，等拍用输入先后。同拍同词音符各有独立的声部内序号 `number`，从 1 开始。任一声部无效时整稿拒绝。发行和读取按约分后的值呈现拍位。

声部版面由有序 `phrases` 构成。每句含 `phrase`、`notes` 及 `marks`。每条标记含 `mark_id`、`position` 和 `text`。含 n 个音符的句有 0 至 n 共 n+1 个音隙。0 为句首，n 为句尾。拆点必须在内部。撤销指定仍在谱面上相邻、未被改动的拆句两侧，产生新修订。原样保留其他句段、音符和历史谱。

呼吸标记的合法音隙以所报固定谱上的句为准。原句仍可显示该音隙时保留原句归属。否则按音乐音隙投到目标句界，恰在新句界且原句消失时归左句尾。拆点两侧的标记合句后在同一内部音隙分别显示。标记写入只更新可投影的已受理记录，固定谱按发行时的内容复领。对历史修订再次发行时，采用该修订的句界与本次发行时已收标记。

收场包含每次成功领过的原谱号，单独发行不构成领用。收场后可复查清单及固定谱。没有领过谱的有效轮次可得到空清单。

## 回归与样例

`examples/draft.json` 含 soprano 的「风过长桥」与 alto 的两条音符。演示用隔离库载入样例，真实分发拆句谱，按固定谱提交拆点两个原址，撤销后再交已准备的右句首请求。演示对照合句内部音隙、历史谱和最后实领的两张谱。完整回归还覆盖严格分拍顺序、等拍同词、过期编辑、原址越界、两种请求到达顺序、同号异参、按号重开与闭场写入失败回滚。
