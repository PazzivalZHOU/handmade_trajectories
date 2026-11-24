# 人工轨迹标注与 DS 输入工具

在机器人相机原图上手画轨迹，导出有顺序的像素点、夹爪意图及可选高度，再转换成冻结 DeepSeek（DS）可读取的输入。

绘制页面完全在本地运行。仅绘制不需要安装 Python、机器人模拟器或模型，也不会自动连接 DS。当前输入适配不更新模型参数，二维路径也不直接等于三维机器人动作。

已提供 [solve_equation、seed 11、第 13 轮完整单次流程](samples/solve_equation_seed11_round13/README.md)：18 张原始观察 PNG，加上全部执行视频逐帧导出的 240 张 PNG，共 258 张图片，覆盖初始、接近抓取、搬运、放置、撤离和结束；另附全部 15 段原始执行视频及顺序索引。

下载仓库后，先看[完整流程与绘制说明](samples/solve_equation_seed11_round13/README.md)，再打开 `sketch.html`。第一次可以导入 `samples/solve_equation_seed11_round13/observations/000000/current_cam_head.png`；观察编号填 `000000`，相机选 `head`，手臂选 `right`，点含义选“夹爪中心经过的位置”。

## 在电脑上绘制

1. Clone 本仓库，或下载 ZIP 后解压。双击 `sketch.html`，用现代浏览器打开。
2. 导入服务器准备的原始 `current_cam_head.png`、`current_cam_left_wrist.png` 或 `current_cam_right_wrist.png`。请保留原始文件字节；转换时会核对图像 SHA256 和分辨率。
3. 根据该图片的说明填写观察编号，例如 `000000`，并选择对应相机和手臂。相机与手臂是两个不同的选项。
4. 选择点的含义：第一次建议选“夹爪中心经过的位置”。若选“物体经过的位置”，整条路径都应追踪物体上的同一个参考位置。
5. 按操作先后拖线或逐点点击。夹爪状态和高度应用于随后新增的点；切换选项不会修改已画的点。
6. 点击“导出轨迹 JSON”，把浏览器下载的 `trajectory-sketch.json` 移入 `annotations/`，改成不会与其他场次重名的文件名。

一个抓取搬运示例：先选“打开”并画接近路径；选“闭合”并在抓取处点击；选“保持”画搬运路径；选“打开”并在释放处点击；最后选“保持”画撤离路径。不要把夹爪路径与物体路径混在同一条轨迹中。

不清楚世界坐标中的高度时先留空，深度会保留为未解析。这里的高度是世界坐标 z，单位米，不是像素高度或相对桌面的距离。即使填写高度，没有匹配标定也不会得到三维坐标。

“撤销一点”只撤销最后一个点；“清空”移除所有点。当前版本没有自动保存或导入旧 JSON 继续编辑的功能，关闭前请导出。一次导出对应一个相机图像和一只手臂。

若导图时提示浏览器不支持图像哈希，可在本机仓库目录启动：

```bash
python -m http.server 8000 --bind 127.0.0.1
```

然后在同一台电脑打开 `http://localhost:8000/sketch.html`。通常直接打开文件即可，无需启动这个服务。

## 用 GitHub 往返同步

推荐独立的私有仓库。服务器提交待标注图片和说明，本地拉取后绘制并提交 JSON，服务器再拉取结果。

```text
服务器准备原图及说明 → Push → 本地 Pull → 浏览器绘制
→ 保存 JSON 到 annotations/ → Commit / Push → 服务器 Pull
```

第一次先 Clone 仓库；每次开始标注前，在本地仓库运行：

```bash
git pull --ff-only
```

导出后将 JSON 放进仓库，再运行：

```bash
git add annotations/task_episode_head_000000.json
git commit -m "Add human trajectory for task episode"
git push
```

请将示例文件名替换成实际保存的文件名。浏览器下载目录不属于仓库，文件需要先移动进来。也可用 GitHub Desktop 完成 Pull、Commit 和 Push。

服务器和电脑都应保留原始图片。同一个观察编号会在不同场次重复，因此请在图片说明、目录和标注文件名里区分任务、场次与相机。图像哈希会随 JSON 自动导出。

建议的文件布局：

```text
samples/task_episode/
  task.txt                      # 任务、场次、观察编号、相机及绘制要求
  current_cam_head.png          # 相机原始 PNG
annotations/
  task_episode_head_000000.json # 人工导出的 JSON
```

`samples/solve_equation_seed11_round13/` 包含该轮完整流程的原始观察、全部视频及逐帧图片、阶段索引和来源哈希。`samples/solve_equation_round13_000000/` 另保留初始时点的三视角快捷样例。它们来自已有历史记录，用于离线标注，不是正在运行的实验现场。仓库没有预填的人工示范轨迹，`annotations/` 用于保存你导出的 JSON。

## 在服务器转换给 DS

以下 Python 依赖仅用于转换和测试，本地浏览器绘制不需要安装。

```bash
python -m pip install -r requirements.txt
```

先转换单个 JSON，并验证原图：

```bash
python trajectory.py annotations/task_episode_head_000000.json \
  --image samples/task_episode/current_cam_head.png \
  --simplify-pixels 2 \
  --output conditioning.json
```

也可生成独立交接目录：

```bash
python trajectory_handoff.py \
  --sketch annotations/task_episode_head_000000.json \
  --observation samples/task_episode \
  --observation-id 000000 \
  --output-dir /path/to/new-input-bundle
```

`--output-dir` 必须是尚不存在的新目录。默认保留转折、折返、夹爪和高度变化，以原图 2 像素容差减少冗余点；使用 `--simplify-pixels 0` 可保留全部点。观察编号、相机图片哈希和实际尺寸必须一致。

交接目录包含 `sketch.json`、`reference.png`、`conditioning.json`、`student_prompt.txt` 和 `manifest.json`。DS 的后续运行需要显式加载这份输入；上传 GitHub 或执行转换不会自动启动模型、模拟器或机器人。

二维像素不能单独确定深度、姿态、抓持和接触关系。可选的 `--calibration` 支持匹配图像哈希的外部相机标定；只有明确填写高度的点才投影到指定高度平面。具体参数参见 `python trajectory.py --help`。

`native_calibration.py` 是原研究服务器的可选桥接器，保留了固定 HexaShell 版本和服务器 checkout 路径，依赖原服务器环境。普通二维绘制、校验、转换及不使用 `--native-calibration` 的交接流程均不需要该环境。

## 验证和来源

```bash
python -m unittest test_trajectory -v
```

`test_trajectory.py` 复用原项目的 12 项轨迹回归测试，覆盖图像绑定、二维深度保留、坐标边界、夹爪语义、减点和交接目录。它们不代表机器人任务成功率。

源文件来自 `experiments/rss_next_stage/`。`SOURCE_MANIFEST.json` 保存原始文件 SHA256；本次提取保持四个工具源文件的内容不变。
