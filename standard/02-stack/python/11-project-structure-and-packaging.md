## 十一、工程结构与打包规范

> 来源：packaging.python.org「src layout vs flat layout」（https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/）；PEP 621「Storing project metadata in pyproject.toml」（https://peps.python.org/pep-0621/）；PEP 518「Specifying Minimum Build System Requirements for Python Projects」（https://peps.python.org/pep-0518/）。

### 两种工程布局

"flat layout"（扁平布局）指把项目的各种配置文件和可导入包（import packages）全部放在顶层目录：

```
.
├── README.md
├── noxfile.py
├── pyproject.toml
├── setup.py
├── awesome_package/
│   ├── __init__.py
│   └── module.py
└── tools/
    ├── generate_awesomeness.py
    └── decrease_world_suck.py
```

"src layout" 与扁平布局的区别是把打算被导入的代码（即 `import awesome_package`）移进一个子目录，这个子目录通常命名为 `src/`：

```
.
├── README.md
├── noxfile.py
├── pyproject.toml
├── setup.py
├── src/
│    └── awesome_package/
│       ├── __init__.py
│       └── module.py
└── tools/
    ├── generate_awesomeness.py
    └── decrease_world_suck.py
```

### 两种布局的关键行为差异（src layout vs flat layout）

- src layout 需要先安装项目才能运行其代码，flat layout 不需要。这意味着 src layout 在开发工作流中多了一个步骤（通常开发时用可编辑安装 editable installation，测试时用常规安装）。
- src layout 有助于防止意外使用开发中的代码副本。Python 解释器把当前工作目录作为导入路径的第一项，如果工作目录里存在与已安装包同名的可导入包，使用的将是工作目录里的版本——这可能导致打包工具的隐蔽错误配置，使某些文件没有被包含进发行包。src layout 把可导入包放在与项目根目录分离的子目录中，确保使用的是已安装的副本。
- src layout 有助于强制可编辑安装只能导入本来就该可导入的文件。当可编辑安装通过向导入路径添加目录的 path configuration file 实现时尤其重要：flat layout 会把其它项目文件（如 README.md、tox.ini）和打包/工具配置文件（如 setup.py、noxfile.py）也放到导入路径上，导致某些 import 在可编辑安装中可用、在常规安装中不可用。

### 从源码直接运行 CLI 的变通方法（src layout）

由于上述 src layout 的特性，命令行接口不能直接从源码树运行，需要先以 Development Mode 安装软件包用于测试。如果这样不方便，一个变通方法是在包的 `__main__.py` 中把包目录前置到 `sys.path`：

```python
import os
import sys

if not __package__:
    # Make CLI runnable from source tree with
    #    python src/package
    package_source_path = os.path.dirname(os.path.dirname(__file__))
    sys.path.insert(0, package_source_path)
```

### pyproject.toml：构建系统最小依赖（PEP 518）

PEP 518 规定项目以声明式方式在 `pyproject.toml`（TOML 格式）文件中列出执行其构建系统所需的最小构建依赖，解决 `setup.py` 的"先有鸡还是先有蛋"问题（不执行 setup.py 就无法知道它的依赖，而执行它又需要先装好依赖；setuptools 的 `setup_requires` 方案因信息只有在执行 setup.py 时才可读、安装时机过晚、不能包含 setuptools 自身、与 pip 的依赖管理冲突等原因被弃用）。构建产物大致分三步：1) 源码检出；2) 安装构建系统；3) 执行构建系统——PEP 518 覆盖第 2 步（第 3 步由 PEP 517 覆盖）。

选择 TOML 的原因：人类可读可编辑（不同于 JSON）、足够灵活（不同于 configparser）、有正式标准（不同于 configparser）、不过度复杂（不同于 YAML）。

`[build-system]` 表用于存储构建相关数据：`requires` 是该表中最初唯一有效且必填的键，其值必须是字符串列表，每个字符串为表示执行构建系统所需依赖的 PEP 508 规范字符串（目前即执行 `setup.py` 所需依赖）：

```toml
[build-system]
# Minimum requirements for the build system to execute.
requires = ["setuptools"]  # PEP 508 specifications.
```

对于绝大多数依赖 setuptools 的 Python 项目，上面的写法就是其 `pyproject.toml`；由于 setuptools 在社区中的广泛使用，当 `pyproject.toml` 不存在时，构建工具应按上述示例配置文件的默认语义行事。工具不应强制要求 `[build-system]` 表的存在——`pyproject.toml` 可能合法地只存储非构建数据而没有 `[build-system]` 表；此时应使用上述默认值。但如果声明了该表却缺少必填字段，工具应视为错误。

`[tool]` 表：任何与 Python 项目相关的工具（不仅是构建工具）的配置数据都可以放在 `[tool]` 下的子表中（例如 flit 工具的配置放在 `[tool.flit]`）。为避免命名冲突，分配规则是：一个项目只有拥有 Cheeseshop/PyPI 上 `$NAME` 条目的所有权，才可以使用 `tool.$NAME` 子表。

本 PEP 未列出的表保留给未来的 PEP 使用。

### 项目核心元数据：`[project]` 表（PEP 621）

PEP 621 规定如何在 `pyproject.toml` 中书写项目的 core metadata 供打包相关工具消费。动机：鼓励以静态方式声明核心元数据（更快、更易声明、无歧义、构建后端可确定性地消费）；提供工具无关的元数据声明方式，便于学习和在构建后端之间迁移。本 PEP 只标准化 core metadata 规范覆盖的、各项目间非常通用的元数据，不试图标准化构建后端需要的全部元数据（例如 wheel 中包含哪些文件的指定方式仍是各构建后端自由的），也不改变底层 core metadata 本身。

规范约束：指定项目元数据时，工具必须（MUST）遵守并尊重本 PEP 规定的元数据；元数据书写不当时工具必须报错。用本 PEP 指定的数据是权威的（canonical）：工具不能（CANNOT）移除、增加或修改已静态指定的数据；只有当字段被标记为 `dynamic` 时工具才可以提供"新"值。

字段（均在 `[project]` 表中）：

- `name`（字符串，对应核心元数据 `Name`）：项目名。工具必须要求用户静态定义此字段；读取后 SHOULD 立即按 PEP 503 规范化以备内部一致性使用。
- `version`（字符串，对应 `Version`）：项目版本，遵循 PEP 440；用户 SHOULD 优先指定已规范化的版本。
- `description`（字符串，对应 `Summary`）：项目的摘要描述。
- `readme`（字符串或表，对应 `Description`）：项目的完整描述（即 README）。字符串形式是含完整描述的文本文件的相对路径，工具必须假定文件编码为 UTF-8；路径以大小写不敏感的 `.md` 结尾时内容类型必须视为 `text/markdown`，以 `.rst` 结尾时视为 `text/x-rst`；无法识别的后缀且未提供 content-type 时必须报错。表形式含 `file` 键（相对路径）或 `text` 键（完整描述本身），二者互斥（同时指定必须报错）；表形式还有 `content-type` 字段，未指定时必须报错，`charset` 参数缺省假定为 UTF-8。
- `requires-python`（字符串，对应 `Requires-Python`）：项目的 Python 版本要求。
- `license`（表，对应 `License`）：可有两个互斥键之一——`file`（含许可证文件的相对路径，编码必须假定为 UTF-8）或 `text`（许可证文本，语义同核心元数据 `License` 字段）。`license` 键的实用字符串取法被有意留白，以便未来 PEP 支持 SPDX 表达式。
- `authors` / `maintainers`（内联表数组，对应 `Author`/`Author-email`/`Maintainer`/`Maintainer-email`）："authors" 的确切含义开放解释（可列原始或主要作者、当前维护者或包的所有人），"maintainers" 类似。数组每项有 `name` 和 `email` 两个可选键，值必须是字符串；`name` 必须是合法的邮件名（RFC 822 中可放在地址前的部分）且不含逗号，`email` 必须是合法邮件地址。填核心元数据时：只有 `name` 进 `Author`/`Maintainer`；只有 `email` 进 `Author-email`/`Maintainer-email`；两者都有则以 `{name} <{email}>` 格式进 `Author-email`/`Maintainer-email`；多个值用逗号分隔。
- `keywords`（字符串数组，对应 `Keywords`）：项目关键词。
- `classifiers`（字符串数组，对应 `Classifier`）：适用于项目的 Trove classifiers。
- `urls`（表，键值均为字符串，对应 `Project-URL`）：URL 表，键是 URL 标签，值是 URL 本身。
- 入口点（表 `[project.scripts]`、`[project.gui-scripts]`、`[project.entry-points]`）：`[project.scripts]` 对应 entry points 规范中的 `console_scripts` 组，键是入口点名称、值是对象引用；`[project.gui-scripts]` 对应 `gui_scripts` 组，格式同上；`[project.entry-points]` 是表的集合，每个子表的名是一个 entry point 组，键值语义同 `[project.scripts]`。用户不得创建嵌套子表，entry point 组只能有一层深。若元数据里出现 `[project.entry-points.console_scripts]` 或 `[project.entry-points.gui_scripts]`，构建后端必须报错（与 `[project.scripts]`/`[project.gui-scripts]` 存在歧义）。
- `dependencies` / `optional-dependencies`（对应 `Requires-Dist` 与 `Provides-Extra`）：`dependencies` 是字符串数组，每个字符串必须是合法的 PEP 508 字符串，直接映射为一个 `Requires-Dist` 条目；`optional-dependencies` 是表，每个键指定一个 extra（键必须是 `Provides-Extra` 的合法值），值为 PEP 508 字符串数组，数组中每个值成为对应 `Provides-Extra` 的 `Requires-Dist` 条目。
- `dynamic`（字符串数组）：声明本 PEP 列出的哪些字段被有意留空、由其他工具动态提供，用于清晰区分"故意未指定"与"稍后由工具提供"。规则：构建后端必须尊重静态指定的元数据（即未列入 `dynamic` 的字段）；`name` 列入 `dynamic` 必须报错；核心元数据规范标为 "Required" 的字段必须静态指定或列入 `dynamic`（否则构建后端必须报错）；标为 "Optional" 的字段 MAY 列入 `dynamic`；某字段既静态指定又列入 `dynamic` 必须报错；未列入 `dynamic` 的字段构建后端不能替用户填写（`dynamic` 是允许工具填充元数据的唯一途径，用户必须显式选择加入）；字段列入 `dynamic` 但构建后端无法提供数据时必须报错。

没有 `[project]` 表隐式表示构建后端将动态提供所有字段。工具想存放自己的配置，应使用 PEP 518 定义的 `[tool]` 表。本 PEP 未标准化构建元数据（sdist/wheel 里包含哪些文件不在范围内）。

PEP 621 完整示例（原文照录）：

```toml
[project]
name = "spam"
version = "2020.0.0"
description = "Lovely Spam! Wonderful Spam!"
readme = "README.rst"
requires-python = ">=3.8"
license = {file = "LICENSE.txt"}
keywords = ["egg", "bacon", "sausage", "tomatoes", "Lobster Thermidor"]
authors = [
  {email = "hi@pradyunsg.me"},
  {name = "Tzu-ping Chung"}
]
maintainers = [
  {name = "Brett Cannon", email = "brett@python.org"}
]
classifiers = [
  "Development Status :: 4 - Beta",
  "Programming Language :: Python"
]

dependencies = [
  "httpx",
  "gidgethub[httpx]>4.0.0",
  "django>2.1; os_name != 'nt'",
  "django>2.0; os_name == 'nt'"
]

[project.optional-dependencies]
test = [
  "pytest < 5.0.0",
  "pytest-cov[all]"
]

[project.urls]
homepage = "https://example.com"
documentation = "https://readthedocs.org"
repository = "https://github.com"
changelog = "https://github.com/me/spam/blob/master/CHANGELOG.md"

[project.scripts]
spam-cli = "spam:main_cli"

[project.gui-scripts]
spam-gui = "spam:main_gui"

[project.entry-points."spam.magical"]
tomatoes = "spam:main_tomatoes"
```

### 推荐的典型工程结构示例

```
my-project/
├── pyproject.toml          # 依赖管理 + 构建配置（替代 setup.py + requirements.txt）
├── README.md               # 项目说明入口
├── .gitignore
├── .env.example            # 环境变量模板
├── docs/                   # 架构设计、API 文档
├── scripts/                # 初始化脚本、数据填充脚本
├── tests/                  # 测试套件
│   ├── conftest.py         # 公共 fixture
│   └── test_main.py
├── src/
│   └── my_project/         # 你的包（所有业务代码在这里）
│       ├── __init__.py
│       ├── __main__.py     # 入口点：python -m my_project
│       ├── config.py       # 配置加载
│       ├── models/         # 数据模型
│       ├── services/       # 业务逻辑
│       ├── api/            # 对外接口（HTTP 路由等）
│       └── utils/          # 工具函数
└── docker/
    └── docker-compose.yml
```

该示例采用 src layout：可导入包 `my_project` 位于 `src/` 之下，符合上文"防止意外使用开发中代码副本、强制可编辑安装只导入本该可导入的文件"两条行为差异；项目元数据与构建配置集中声明于 `pyproject.toml`（见上文 PEP 518/621）。
