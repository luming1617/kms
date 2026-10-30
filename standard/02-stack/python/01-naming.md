## 一、命名规范

> 来源：Google Python 风格指南（中文版）「Python风格规范-命名」（https://zh-google-styleguide.readthedocs.io/en/latest/google-python-styleguide/python_style_rules.html#id15）；PEP 8（中文版）「命名约定」（https://peps.pythonlang.cn/pep-0008/#naming-conventions）。

### 各类元素按固定风格命名

模块名 `module_name`、包名 `package_name`、类名 `ClassName`、方法名 `method_name`、异常名 `ExceptionName`、函数名 `function_name`（如 `query_proper_noun_for_thing`、`send_acronym_via_https`）、全局常量名 `GLOBAL_CONSTANT_NAME`、全局变量名 `global_var_name`、实例变量名 `instance_var_name`、函数参数名 `function_parameter_name`、局部变量名 `local_var_name`。

函数名、变量名和文件名应该是描述性的，避免缩写；特别要避免对项目之外的人有歧义或不熟悉的缩写，也不要通过省略单词中的字母来进行缩写。必须用 `.py` 作为文件后缀名，不要用连字符。

类名应使用首字母大写的形式（CapWords），模块名用小写加下划线的形式（lower_with_under.py）。旧模块的 CapWords.py 形式不再鼓励——模块名和类名相同会让人困惑（"我刚刚写的是 `import StringIO` 还是 `from StringIO import StringIO`？"）。新的单元测试文件遵守 PEP 8，用小写加下划线格式的方法名，例如 `test_<被测试的方法名>_<状态>`。

### 描述性的命名风格清单（PEP 8）

通常区分以下命名风格：`b`（单个小写字母）、`B`（单个大写字母）、`lowercase`、`lower_case_with_underscores`、`UPPERCASE`、`UPPER_CASE_WITH_UNDERSCORES`、`CapitalizedWords`（CapWords/CamelCase，也称 StudlyCaps；在 CapWords 中使用首字母缩写时，请将首字母缩写的所有字母大写，因此 `HTTPServerError` 比 `HttpServerError` 更好）、`mixedCase`（与 CapitalizedWords 的区别在于首字母小写）、`Capitalized_Words_With_Underscores`（丑陋！）。

使用前导或尾随下划线的特殊形式（可与任何大小写约定组合）：
- `_single_leading_underscore`：弱"内部使用"指示符，`from M import *` 不导入以下划线开头的对象。
- `single_trailing_underscore_`：约定俗成地用于避免与 Python 关键字冲突，如 `tkinter.Toplevel(master, class_='ClassName')`。
- `__double_leading_underscore`：命名类属性时触发名称重整（类 `FooBar` 内部，`__boo` 变为 `_FooBar__boo`）。
- `__double_leading_and_trailing_underscore__`："魔术"对象或属性，如 `__init__`、`__import__`、`__file__`。切勿自行创建此类名称，仅按文档说明使用。

### 需要避免的名称

- 只有单个字符的名称，除以下特别批准的情况：计数器和迭代器（`i`、`j`、`k`、`v` 等）、`try/except` 中代表异常的 `e`、`with` 语句中代表文件句柄的 `f`、私有的无约束类型变量（`_T = TypeVar("_T")`、`_P = ParamSpec("_P")`）。
- 包含连字符（`-`）的包名/模块名。
- 首尾均为双下划线的名称（Python 保留名称）。
- 包含冒犯性词语的名称。
- 在不必要的情况下包含变量类型的名称（例如 `id_to_name_dict`）。
- （PEP 8）切勿将字符 `l`（小写 el）、`O`（大写 oh）、`I`（大写 eye）用作单字符变量名——某些字体中与数字 1 和 0 无法区分；想用 `l` 时改用 `L`。

### 内部与公有：下划线前缀

"内部"表示仅在模块内可用，或在类内是受保护/私有的。在一定程度上，名称前加单下划线（`_`）可以保护模块变量和函数（格式检查器会对受保护的成员访问发出警告）。

在实例变量或方法名称前加双下划线可将其变成类私有成员（基于名称修饰 name mangling），但 Google 规范不鼓励这种用法：严重影响可读性和可测试性，且没有真正实现私有，建议使用单下划线。

### 按类型划分的命名规范（Google，依据 Python 之父 Guido 的建议所制定）

| 类型 | 公有 | 内部 |
|---|---|---|
| 包 | `lower_with_under` | |
| 模块 | `lower_with_under` | `_lower_with_under` |
| 类 | `CapWords` | `_CapWords` |
| 异常 | `CapWords` | |
| 函数 | `lower_with_under()` | `_lower_with_under()` |
| 全局常量/类常量 | `CAPS_WITH_UNDER` | `_CAPS_WITH_UNDER` |
| 全局变量/类变量 | `lower_with_under` | `_lower_with_under` |
| 实例变量 | `lower_with_under` | `_lower_with_under` |
| 方法名 | `lower_with_under()` | `_lower_with_under()` |
| 函数参数/方法参数 | `lower_with_under` | |
| 局部变量 | `lower_with_under` | |

### PEP 8 具体命名约定

- 首要原则：作为 API 公共部分对用户可见的名称应遵循反映用法而非实现的约定。
- 包和模块：模块应该使用简短、全小写的名称，能提高可读性时可用下划线；包同样简短全小写但不鼓励下划线。C/C++ 扩展模块带前导下划线（如 `_socket`）。
- 类名通常用 CapWords；接口被文档化并主要用作可调用对象时，可用函数命名约定。内置名称有单独约定：大多数是单字（或两词连写），CapWords 仅用于异常名称和内置常量。
- 类型变量名称（PEP 484）通常用 CapWords，首选短名称 `T`、`AnyStr`、`Num`；用于声明协变或逆变行为的变量建议加 `_co` 或 `_contra` 后缀，如 `VT_co = TypeVar('VT_co', covariant=True)`。
- 异常应用类的命名约定；如果该异常确实是错误，名称应加 `Error` 后缀。
- 全局变量：约定与函数大致相同（希望这些变量仅用于一个模块内部）。旨在通过 `from M import *` 使用的模块应使用 `__all__` 防止导出全局变量，或使用较旧约定为此类全局变量加下划线前缀。
- 函数和变量名称：小写，必要时用下划线分隔单词提高可读性；变量名遵循与函数名相同的约定。`mixedCase` 仅在已经是主要风格的上下文中允许（如 `threading.py`），以保持向后兼容性。
- 函数和方法参数：实例方法第一个参数始终用 `self`，类方法第一个参数始终用 `cls`。参数名与保留关键字冲突时，附加尾随下划线优于缩写或拼写错误（`class_` 优于 `clss`；更好的方法是使用同义词）。
- 方法名称和实例变量：使用函数命名规则；非公共方法和实例变量用一个前导下划线；为避免与子类发生名称冲突，用两个前导下划线调用名称重整规则（类 `Foo` 的属性 `__a` 无法通过 `Foo.__a` 访问，可通过 `Foo._Foo__a` 访问）。双前导下划线只应用于设计用于子类化的类。
- 常量：通常在模块级别定义，全大写字母书写、下划线分隔单词，示例 `MAX_OVERFLOW`、`TOTAL`。
- ASCII 兼容性：标准库中使用的标识符必须符合 PEP 3131 策略部分描述的 ASCII 兼容性。

### 为继承而设计（PEP 8）

始终决定一个类的方法和实例变量（统称"属性"）应该是公共的还是非公共的；如有疑问，选择非公共——以后把非公共设为公共比反过来更容易。公共属性是不相关客户端预期会使用的、承诺避免向后不兼容更改的属性；非公共属性不保证不会更改甚至被删除。Python 中没有属性是真正的私有。

对于"子类 API"（其他语言称"受保护"）的类，要明确决定哪些属性是公共的、哪些是子类 API、哪些只供基类使用。规则：
- 公共属性不应有前导下划线。
- 公共属性名称与保留关键字冲突时，在名称后附加尾随下划线（优于缩写或错误拼写；但 `cls` 仍是任何已知为类的变量或参数的首选拼写，尤其类方法第一个参数）。
- 简单的公共数据属性最好只公开属性名称，不使用复杂的访问器/修改器方法；需要功能行为时用 `property` 把功能实现在简单数据属性访问语法后面。尽量保持功能行为无副作用（缓存这类副作用通常可以）；避免将属性用于计算开销大的操作——属性表示法让调用者认为访问是相对廉价的。
- 类旨在被子类化且有不希望子类使用的属性时，用两个前导下划线且无尾随下划线命名，触发名称重整（重整只用简单类名，子类同时选择相同类名和属性名仍可能冲突；重整使调试和 `__getattr__()` 不便，但算法有详细文档且易于手动执行；并非所有人都喜欢名称重整，需权衡意外名称冲突与高级调用者的使用）。

### 公共和内部接口（PEP 8）

任何向后兼容性保证仅适用于公共接口，用户必须能清楚区分公共接口和内部接口。已文档化的接口被视为公共接口，除非文档明确声明它们是临时或内部接口；所有未文档化的接口都应假定为内部接口。

模块应使用 `__all__` 属性明确声明其公共 API 中的名称（`__all__` 设为空列表表示没有公共 API）。即使 `__all__` 设置得当，内部接口（包、模块、类、函数、属性或其他名称）仍应以单个前导下划线为前缀；如果任何包含命名空间被认为是内部的，该接口也被认为是内部的。导入的名称应始终被视为实现细节，其他模块不得依赖对这些导入名称的间接访问，除非它们是包含模块 API 的明确文档化部分（如 `os.path`，或包的 `__init__` 模块暴露子模块功能）。

### 【参考】数学符号命名

对于涉及大量数学内容的代码，如果相关论文或算法中有对应的符号，可以忽略以上命名规范并使用较短的变量名；但应在注释或文档字符串中注明所用命名规范的来源，原文无法访问时应在文档中清楚记录。建议公开 API 仍使用符合 PEP 8 的描述性名称，因为使用 API 的代码很可能缺少相关上下文信息。

### 模块组织

应该把相关的类和顶级函数放在同一个模块里；与 Java 不同，不必限制一个模块只有一个类。
