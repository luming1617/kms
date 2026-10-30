## 九、类型注解

> 来源：Google Python 风格指南（中文版）「Python语言规范：代码类型注释」「Python风格规范：类型注解 (type annotation) / 代码类型注释」；PEP 8（中文版）「编程建议：函数注解 / 变量注解」。

### 总则

可以根据 PEP-484 对 Python 3 代码进行注释，并使用诸如 `pytype` 之类的类型检查工具来检查代码。类型注释既可以写在源码里，也可以写在 `pyi`（stub 文件）中：推荐尽量写在源码里；对于第三方代码和扩展包，请使用 pyi 文件。

强烈推荐在更新代码时启用 Python 类型分析：在添加或修改公开 API 时，请添加类型注释，并在构建系统（build system）中启用 pytype。由于 Python 静态分析是新功能，一些意外的副作用（例如类型推导错误）可能会阻碍项目采纳这一功能；建议作者在 BUILD 文件或者代码中添加一个 TODO 注释或者链接，描述那些阻碍采用类型注释的问题。

PEP 8：函数注解应使用 PEP 484 语法；以前在本 PEP 中推荐的注解风格实验不再受到鼓励；但在标准库之外，现在鼓励在 PEP 484 规则内进行实验（例如用 PEP 484 风格类型注解标记大型第三方库或应用程序）。Python 标准库在采用此类注解方面应保持保守，但新代码和大型重构允许使用它们。类型检查器是可选的独立工具——Python 解释器默认不应因类型检查而发出任何消息，也不应根据注解改变其行为；不想使用类型检查器的用户可以自由忽略它们。预计第三方库包的用户可能希望对这些包运行类型检查器，为此 PEP 484 建议使用存根文件：`.pyi` 文件由类型检查器优先于相应的 `.py` 文件读取，可与库一起分发，也可经库作者许可通过 typeshed 仓库单独分发。

### 通用规则（Google）

1. 熟读 PEP-484。
2. 仅在有额外类型信息时才需要注解方法中 `self` 或 `cls` 的类型，例如 `@classmethod def create(cls: Type[_T]) -> _T: ...`。
3. 不需要注解 `__init__` 的返回值（只能返回 `None`）。
4. 对于其他不需要限制变量类型或返回类型的情况，应该使用 `Any`。
5. 无需注解模块中的所有函数：至少需要注解你的公开 API；可自行权衡保证代码的安全性和清晰性与兼顾灵活性；应该注解那些容易出现类型错误的代码（比如曾经出现过错误或疑难杂症）、晦涩难懂的代码、类型已经确定的代码——多数情况下，即使注解了成熟的代码中所有的函数，也不会丧失太多灵活性。

### 换行

尽量遵守前文所述的缩进规则。添加类型注解后，很多函数签名会变成每行一个参数的形式；若要让返回值单独成行，可以在最后一个参数尾部添加逗号：

```python
def my_method(
    self,
    first_var: int,
    second_var: Foo,
    third_var: Bar | None,
) -> int:
    ...
```

尽量在变量之间换行，避免在变量和类型注解之间换行；若所有东西可以挤进一行，也可以接受。若最后一个参数加上返回值的类型注解太长，可以换行并添加 4 格缩进；建议每个参数和返回值都在单独的一行里，并且右括号和 `def` 对齐。返回值类型和最后一个参数也可以放在同一行（可以接受）；把右括号放在新行上、与左括号对齐允许但可读性更差（错误）：

```python
# 错误:
def my_method(self,
              other_arg: MyLongType | None,
             ) -> dict[OtherLongType, MyLongType]:
    ...
```

尽量不要在类型注解中间换行；但有时注解过长以至于一行放不下，此时尽量保持子类型中间不换行。若某个名称和对应的类型注解过长，可以考虑用别名（alias）代表类型；下策是在冒号后换行并添加 4 格缩进（正确），在模块名和类型名之间断开是错误。

### 前向声明（forward declaration）

若需要使用一个尚未定义的类名（比如想在声明一个类时使用自身的类名），可以使用 `from __future__ import annotations` 或者用字符串来代表类名：

```python
from __future__ import annotations

class MyClass:
    def __init__(self, stack: Sequence[MyClass], item: OtherClass) -> None:
        ...

class OtherClass:
    ...
```

或 `def __init__(self, stack: Sequence['MyClass'], item: 'OtherClass') -> None: ...`。

### 默认值

根据 PEP-008，只有对于同时拥有类型注解和默认值的参数，`=` 的周围应该加空格。`def func(a: int = 0) -> int` 正确；`def func(a:int=0) -> int` 错误。

### NoneType

在 Python 的类型系统中，`NoneType` 是"一等"类型，在类型注解中 `None` 是 `NoneType` 的别名。如果一个变量可能为 `None`，则必须声明这种情况！可以使用 `|` 这样的并集 (union) 类型表达式（推荐在新的 Python 3.10+ 代码中使用）或者老的 `Optional` 和 `Union` 语法。应该用显式的 `X | None` 替代隐式声明——早期的 PEP 484 允许将 `a: str = None` 解释为 `a: str | None = None`，但这不再是推荐的行为。

```python
# 正确:
def modern_or_union(a: str | int | None, b: str | None = None) -> str: ...
def union_optional(a: Union[str, int, None], b: Optional[str] = None) -> str: ...

# 错误:
def nullable_union(a: Union[None, str]) -> str: ...  # 用 Union 代替 Optional
def implicit_optional(a: str = None) -> str: ...     # 隐式 Optional
```

### 类型别名（alias）

可以为复杂的类型声明一个别名，别名的命名应该采用大驼峰（例如 `CapWorded`）；若别名仅在当前模块使用，应在名称前加 `_` 代表私有（例如 `_Private`）。注意 `: TypeAlias` 类型注解只能在 3.10 以后的版本使用：

```python
from typing import TypeAlias

_LossAndGradient: TypeAlias = tuple[tf.Tensor, tf.Tensor]
ComplexTFMap: TypeAlias = Mapping[str, _LossAndGradient]
```

### 忽略类型检查

可以使用特殊的注释 `# type: ignore` 禁用某一行的类型检查。对于希望以不同方式使用函数注解的代码，建议在文件顶部附近添加 `# type: ignore` 注释，这会告诉类型检查器忽略所有注解。`pytype` 有针对特定错误的禁用选项（类似格式检查器）：`# pytype: disable=attribute-error`。

### 标注变量的类型

PEP 526 引入了变量注解：模块级变量、类和实例变量以及局部变量的注解，冒号后应有一个空格，冒号前不应有空格；如果赋值有右侧，则等号两侧应各有一个空格：

```python
# 正确:
code: int

class Point:
    coords: Tuple[int, int]
    label: str = '<unknown>'

# 错误:
code:int  # No space after colon
code : int  # Space before colon

class Test:
    result: int=0  # No spaces around equality sign
```

如果难以自动推理某个内部变量的类型，可以用带类型注解的赋值操作来指定类型（在变量名和值的中间添加冒号和类型，类似于有默认值的函数参数）：`a: Foo = SomeUndecoratedFunction()`。你可能在代码仓库中看到残留的 `# type: <类型>` 行尾注释（在 Python 3.6 之前必须这样写注释），但是不要再添加了。尽管 PEP 526 已被 Python 3.6 接受，变量注解语法是所有 Python 版本中存根文件的首选语法（参见 PEP 484）。

### 元组还是列表

有类型的列表中只能有一种类型的元素；有类型的元组可以有相同类型的元素或者若干个不同类型的元素，后面这种情况多用于注解返回值的类型（实际 Python 中 list 和 tuple 都可以放不同类型元素的引用）：

```python
a: list[int] = [1, 2, 3]
b: tuple[int, ...] = (1, 2, 3)
c: tuple[int, str, float] = (1, "2", 3.5)
```

### 类型变量（type variable）

Python 的类型系统支持泛型 (generics)，常见方式是利用类型变量 `TypeVar` 和 `ParamSpec`。`TypeVar` 可以有约束条件（`AddableType = TypeVar("AddableType", int, float, str)`）；`AnyStr` 是 `typing` 模块中常用的预定义类型变量，可用于注解那些接受 `bytes` 或 `str` 但是必须保持一致的类型。

类型变量必须有描述性的名称，除非同时满足以下所有标准：外部不可见、没有约束条件。

```python
# 正确:
_T = TypeVar("_T")
_P = ParamSpec("_P")
AddableType = TypeVar("AddableType", int, float, str)
AnyFunction = TypeVar("AnyFunction", bound=Callable)

# 错误:
T = TypeVar("T")
P = ParamSpec("P")
_T = TypeVar("_T", int, float, str)
_F = TypeVar("_F", bound=Callable)
```

### 字符串类型

不要在新代码中使用 `typing.Text`（只能用于处理 Python 2/3 的兼容问题）。用 `str` 表示字符串/文本数据，用 `bytes` 处理二进制数据。若一个函数中的字符串类型始终一致（比如上述代码中返回值类型和参数类型相同），应该使用 `AnyStr`。

### 导入类型

为了静态分析和类型检查而导入 `typing` 和 `collections.abc` 模块中的符号时，一定要导入符号本身，这样常用的类型注解更简洁、也符合全世界的习惯；可以在一行内从这两个模块导入多个特定的类（`from collections.abc import Mapping, Sequence`、`from typing import Any, Generic`）。采用这种方法时，导入的类会进入本地命名空间，因此所有 `typing` 和 `collections.abc` 模块中的名称都应该和关键词 (keyword) 同等对待，不能在自己的代码中定义相同的名字；若类型名和某模块中已有的名称出现冲突，可以用 `import x as y` 的形式（`from typing import Any as AnyType`）。

只要可行，就使用内置类型：利用 Python 3.9 引入的 PEP-585，可以在类型注解中使用参数化的容器类型（`def generate_foo_scores(foo: set[str]) -> list[float]`）。使用 Apache Beam 这样没有为 PEP 585 更新的代码，或者代码需要在 Python 3.9 以下版本中运行时，才使用旧风格（`from typing import Set, List`）。

### 有条件的导入

仅在一些特殊情况下（比如在运行时必须避免导入类型检查所需的模块）才能有条件地导入，不推荐这种写法，替代方案是重构代码使类型检查所需的模块可以在顶层导入。可以把仅用于类型注解的导入放在 `if TYPE_CHECKING:` 语句块内，规则：
1. 在类型注解中，有条件地导入的类型必须用字符串表示，这样才能和 Python 3.6 之前的代码兼容（Python 3.6 之前真的会对类型注解求值）。
2. 只有那些仅仅用于类型注解的实例才能有条件地导入，别名也是如此，否则会引发运行时错误（运行时不会导入这些模块）。
3. 有条件的导入语句应紧随所有常规导入语句之后。
4. 有条件的导入语句之间不能有空行。
5. 和常规导入一样，请对有条件的导入语句排序。

```python
import typing
if typing.TYPE_CHECKING:
    import sketch
def f(x: "sketch.Sketch"): ...
```

### 循环依赖

若类型注解引发了循环依赖，说明代码可能存在问题，这样的代码适合重构；虽然技术上可以支持循环依赖，但很多构建系统不支持。可以用 `Any` 替换引起循环依赖的模块：起一个有意义的别名，然后使用模块中的真实类型名（`Any` 的任何属性依然是 `Any`）；定义别名的语句应该和最后一行导入语句之间间隔一行：

```python
from typing import Any

some_mod = Any  # 因为 some_mod.py 导入了我们的模块.
...

def my_method(self, var: "some_mod.SomeType") -> None:
    ...
```

### 泛型（generics）

在注解类型时，尽量为泛型类型填入类型参数，否则泛型参数默认为 `Any`。`def get_names(employee_ids: Sequence) -> Mapping` 实际表示 `Sequence[Any] -> Mapping[Any, Any]`（错误）。如果泛型类型的参数的确应该是 `Any`，请显式地标注；不过注意 `TypeVar` 很可能更合适：

```python
# 错误:
def get_names(employee_ids: Sequence[Any]) -> Mapping[Any, str]:
    """返回员工ID到员工名的映射."""

# 正确:
_T = TypeVar('_T')
def get_names(employee_ids: Sequence[_T]) -> Mapping[_T, str]:
    """返回员工ID到员工名的映射."""
```
