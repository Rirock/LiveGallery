# LiveGallery

一个简单好用的 Windows 11 本地相册软件。  
把手机照片复制到电脑后，也可以像手机相册一样轻松浏览。

下载链接：
https://pan.baidu.com/s/1MbK_Ztu-l_pVSDhV-agQ_Q?pwd=9wre 提取码: 9wre
---

## 使用说明

LiveGallery 是一个面向普通用户的本地相册应用。

它主要解决几个很常见的问题：

- 手机照片复制到电脑后，顺序很乱
- Windows 文件夹看照片不够方便
- 动态照片在电脑上不好播放
- 视频、照片混在一起，不像手机相册那样直观
- 不想上传云盘，只想在自己电脑里看照片

LiveGallery 会扫描你选择的照片文件夹，然后在本地生成相册索引，让你更方便地浏览照片和视频。

---

## 主要特点

### 像手机相册一样浏览

LiveGallery 支持图片网格浏览，可以快速查看大量照片。  
照片会尽量按照真实拍摄时间排序，而不是简单按照文件名排序。

排序时会优先读取：

1. 照片 EXIF 拍摄时间
2. 文件名中的时间信息
3. 文件修改时间

这样从手机复制到电脑的照片，也能尽量保持正确顺序。

---

### 支持小米动态照片

LiveGallery 支持识别小米手机的动态照片。

如果 JPG 里面包含 Motion Photo / MicroVideo / XMP 信息，软件可以识别出来，并在需要播放时提取里面的视频。

动态照片不会在扫描时全部提取，只有点击播放时才会生成缓存，避免一开始占用太多空间。

---

### 支持照片和视频

目前支持常见格式：

- jpg
- jpeg
- png
- heic
- heif
- mp4
- mov

可以浏览照片，也可以播放普通视频和动态照片视频。

---

### 不修改原始照片

LiveGallery 不会修改、删除或移动你的原始照片。

它只会在本地生成：

- 照片索引
- 缩略图缓存
- 动态照片播放缓存

如果不想用了，删除软件和缓存即可，原始照片不会受到影响。

---

## 功能一览

- 本地照片文件夹扫描
- 按真实拍摄时间排序
- 图片网格浏览
- 时间轴跳转
- 文件名搜索
- 收藏照片
- 多选照片
- 复制照片到资源管理器
- 图片预览
- 滚轮缩放
- 视频播放
- 动态照片播放
- SQLite 本地索引
- 本地缩略图缓存

---

## 适合谁用？

LiveGallery 比较适合：

- 经常把手机照片复制到 Windows 电脑的人
- 想在电脑上像手机一样看照片的人
- 使用小米手机，想查看动态照片的人
- 不想把照片上传到云端的人
- 不懂 Python，只想直接双击使用的人
- 想找一个简单本地相册软件的人

---

## 普通用户怎么用？

普通用户建议直接使用打包好的 Windows 便携版。

下载后解压，打开：

```text
LiveGallery.exe
```

然后进入设置，选择你的照片文件夹即可。

常用操作：

- `更新`：只扫描新增或变化的照片，速度比较快
- `重新扫描`：重新建立当前目录索引，不会影响原始照片

---

## 从源码运行

如果你想自己从源码运行，可以按照下面步骤操作。

### 1. 环境要求

推荐环境：

- Windows 11
- Python 3.11 或 Python 3.12

### 2. 下载源码

从 GitHub 下载项目源码：

```text
https://github.com/Rirock/LiveGallery
```

下载后解压，并进入项目目录。

### 3. 创建虚拟环境

在项目目录中打开 PowerShell：

```powershell
python -m venv .venv
```

启用虚拟环境：

```powershell
.venv\Scripts\Activate.ps1
```

### 4. 安装依赖

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 5. 启动程序

```powershell
python -m src.main
```

如果窗口正常打开，就说明运行成功。

---

## 打包 Windows 便携版

如果想自己打包，可以运行：

```powershell
.\build_portable.ps1
```

打包完成后会生成：

```text
release\LiveGallery\
release\LiveGallery-portable-windows.zip
release\LiveGallery-source.zip
```

说明：

- `release\LiveGallery\` 是便携版程序目录
- `LiveGallery-portable-windows.zip` 可以发给其他 Windows 用户
- `LiveGallery-source.zip` 是源码压缩包

打包文件不会包含你的照片索引、缩略图缓存或日志。

---

## 项目目录

```text
.
├─ assets/
├─ src/
│  ├─ main.py
│  ├─ app_paths.py
│  ├─ models/
│  ├─ services/
│  ├─ views/
│  └─ widgets/
├─ logo.png
├─ requirements.txt
├─ build_portable.ps1
└─ README.md
```

---

## 缓存说明

LiveGallery 的缓存默认在项目目录下：

```text
cache/gallery.db
cache/thumbnails/
cache/motion_photos/
```

含义：

- `gallery.db`：照片索引数据库
- `thumbnails/`：缩略图缓存
- `motion_photos/`：动态照片视频缓存

缓存可以删除。  
删除后，LiveGallery 会在需要时重新生成。

---

## 说明

LiveGallery 目前是一个轻量级本地相册项目，重点是简单、直观、好用。

它不追求复杂的云相册功能，也不会把照片上传到服务器。  
所有索引、缩略图和缓存都保存在本地电脑中。
