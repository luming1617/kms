## 四、导入、包与静态检查

> 来源：Google Python 风格指南（中文版）「Python语言规范：Lint / 导入 / 包 / 现代python: from __future__ imports」「Python风格规范：导入 (import) 语句的格式」；PEP 8（中文版）「导入 / 模块级双下划线名称」。

### Lint：一定要用 pylint 检查代码

pylint 是在 Python 代码中寻找 bug 和格式问题的工具，可以发现疏忽，例如拼写错误、使用未赋值的变量等。出于 Python 的动态特性，部分警告可能有误，不过误报应该不常见。要利用其优势，有时需要绕过它、抑制它的警告或改进它。结论：一定要用 pylint 检查你的代码（用谷歌提供的 `pylintrc` 运行）。

抑制不恰当的警告，以免其他问题被警告淹没；用行注释来抑制，例如：

```python
def do_PUT(self):  # WSGI 接口名, 所以 pylint: disable=invalid-name
    ...
```

pylint 的警告均以符号名（如 `empty-docstring`）来区分，谷歌特有的警告以 `g-` 为前缀；如果警告的符号名不够见名知意，那么请添加注释。这种抑制方式的好处是可以轻易搜索并重新评判这些注释。可用 `pylint --list-msgs` 列出所有警告，用 `pylint --help-msg=invalid-name` 查询某个警告的详情。相较于旧格式 `pylint: disable-msg`，推荐使用 `pylint: disable`。

如果有"参数未使用"的警告，可以在函数体开头用 `del` 删除无用的变量以消除警告，一定要用注释说明原因（注明"未使用."即可）：

```python
def viking_cafe_order(spam: str, beans: str, eggs: str | None = None) -> str:
    del beans, eggs  # 未被维京人使用.
    return spam + spam + spam
```

其他常用方法——用 `_` 作为未使用参数的名称、给参数名加前缀 `unused_`、或把它们赋值给变量 `_`——允许但不再推荐：这会导致调用者无法通过参数名传参，也不能保证变量确实没被引用。

### 导入语句的使用方式（Google）

使用 `import` 语句时，只导入包和模块，而不单独导入函数或者类。命名空间的管理规范十分简单：`x.Obj` 表示 `Obj` 对象定义在模块 `x` 中。

1. 用 `import x` 来导入包和模块。
2. 用 `from x import y`，其中 x 是包前缀，y 是不带前缀的模块名。
3. 在以下情况使用 `from x import y as z`：如果有两个模块都叫 `y`；如果 `y` 和当前模块的某个全局名称冲突；如果 `y` 是长度过长的名称。
4. 仅当缩写 `z` 是标准缩写时才能使用 `import y as z`（比如 `np` 代表 `numpy`）。

例如导入模块 `sound.effects.echo`：

```python
from sound.effects import echo
...
echo.EchoFilter(input, output, delay=0.7, atten=4)
```

导入时禁止使用相对包名。即使模块在同一个包中，也要使用完整包名，这能避免无意间重复导入同一个包。例外：用于静态分析和类型检查的 `typing`、`collections.abc`、`typing_extensions` 模块，以及 `six.moves` 模块中的重定向。

### 包：使用完整路径名导入（Google）

所有新的代码都应该用完整包名来导入每个模块，避免模块名冲突、或因模块搜索路径与作者的想法不符而导入错误的包，也更容易找到模块。

```python
# 正确: 在代码中引用完整名称 absl.flags (详细版).
import absl.flags
from doctor.who import jodie

_FOO = absl.flags.DEFINE_string(...)

# 正确: 在代码中仅引用模块名 flags (常见情况).
from absl import flags
from doctor.who import jodie

_FOO = flags.DEFINE_string(...)

# 错误: (假设当前文件和 jodie.py 都在目录 doctor/who/ 下)
# 没有清晰地表达作者想要导入的模块和最终导入的模块.
# 实际导入的模块取决于由外部环境控制的 sys.path.
import jodie
```

不能臆测 `sys.path` 包含主程序所在的目录，即使这种环境的确存在。代码必须认定 `import jodie` 表示的是名为 `jodie` 的第三方库或者顶层的包，而非当前目录的 `jodie.py`。

### 导入语句的格式与分组

- Google：导入语句应该各自独占一行（`import os, sys` 错误），`typing` 和 `collections.abc` 的导入除外（`from typing import Any, NewType`、`from collections.abc import Mapping, Sequence` 正确）。导入语句必须在文件顶部，位于模块的注释和文档字符串之后、全局变量和全局常量之前，并按从通用到特殊分组：
  1. 导入 Python 的 `__future__`（如 `from __future__ import annotations`）；
  2. 导入 Python 的标准库（如 `import sys`）；
  3. 导入第三方（PyPI）模块和包（如 `import tensorflow as tf`）；
  4. 导入代码仓库中的子包（如 `from otherproject.ai import mind`）；
  5. 已废弃的规则：导入应用专属的、与该文件属于同一个子包的模块——新代码忽略这条规则，同等对待应用专属的子包和其他子包即可。
- 在每个分组内部，应该按照模块完整包路径（`from path import ...` 中的 `path`）的字典序排序，忽略大小写；可以选择在分组之间插入空行。
- PEP 8：导入通常应放在单独的行上（不过 `from subprocess import Popen, PIPE` 这样说也行）；导入应按以下顺序分组，并在每组导入之间留一个空行：标准库导入、相关的第三方导入、本地应用程序/库特定的导入。

### 绝对导入与相对导入（PEP 8）

建议使用绝对导入，因为它们通常更具可读性，并且在导入系统配置不正确（例如当包内的目录最终出现在 `sys.path` 上）时，它们的行为往往更好（或至少给出更好的错误消息）：

```python
import mypkg.sibling
from mypkg import sibling
from mypkg.sibling import example
```

然而，显式相对导入是绝对导入的可接受替代方案，尤其是在处理复杂的包布局时、使用绝对导入会不必要地冗长的场合（`from . import sibling`、`from .sibling import example`）。标准库代码应避免复杂的包布局，并始终使用绝对导入。

当从包含类的模块导入类时，通常可以拼写为 `from myclass import MyClass`、`from foo.bar.yourclass import YourClass`；如果这种拼写导致局部名称冲突，那么明确地拼写为 `import myclass`、`import foo.bar.yourclass`，并使用 `myclass.MyClass` 和 `foo.bar.yourclass.YourClass`。

应避免使用通配符导入（`from <module> import *`），因为它们使命名空间中存在的名称不明确，会混淆读者和许多自动化工具。通配符导入有一个合理的用例：将内部接口作为公共 API 的一部分重新发布（例如用可选加速器模块的定义覆盖接口的纯 Python 实现，并且事先不知道具体会覆盖哪些定义）；以这种方式重新发布名称时，关于公共和内部接口的指南仍然适用。

### 模块级双下划线名称（PEP 8）

模块级"双下划线"名称（具有两个前导和两个尾随下划线），如 `__all__`、`__author__`、`__version__` 等，应放在模块文档字符串之后，但除 `from __future__` 导入之外的任何导入语句之前。Python 规定 future-imports 必须出现在模块中、位于除文档字符串之外的任何其他代码之前：

```python
"""This is the example module.

This module does stuff.
"""

from __future__ import barry_as_FLUFL

__all__ = ['a', 'b', 'c']
__version__ = '0.1'
__author__ = 'Cardinal Biggles'

import os
import sys
```

### 现代 Python：from __future__ imports（Google）

可以通过导入 `__future__` 包，在较老的运行时上启用新语法，并且只在特定文件上生效。该功能可以让版本升级过程更稳定：可以逐步修改各个文件，并用兼容性声明来防止退化（regression）；现代的代码便于维护，因为不容易积累阻碍运行时升级的技术债。缺点是此类代码无法在过老的运行时上运行。

鼓励使用 `from __future__ import` 语句，源代码从今天起就能使用更现代的 Python 语法；当不再需要支持老版本时，自行删除这些导入语句。如果代码要支持 3.5 版本（而不是常规的 >=3.7），请导入 `from __future__ import generator_stop`。除非你确定代码的运行环境已经足够现代，否则不要删除 future 语句；即使你用不到它们，也要保留，以免其他编辑者不小心对旧的特性产生依赖。在你认为恰当的时候，可以使用其他来自 `from __future__` 的语句。
