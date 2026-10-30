## 六、表达式、容器与函数特性

> 来源：Google Python 风格指南（中文版）「Python语言规范：推导式和生成式 / 默认迭代器和操作符 / 生成器 / Lambda函数 / 条件表达式 / 默认参数值 / 特性 (properties) / True/False的求值」「Python风格规范：访问器 (getter) 和设置器 (setter)」。

### 推导式和生成式：适用于简单情况

列表、字典和集合的推导式和生成式可以用于简洁高效地创建容器和迭代器，而无需借助循环、`map()`、`filter()` 或者 `lambda`（元组没有推导式，`()` 内加类似推导式的句式返回的是生成器）。简单的列表推导式更加清晰和简洁；生成器表达式十分高效，因为无需创建整个列表；但复杂的推导式和生成式难以理解。

可以用于简单情况：以下每个部分不应超过一行——映射表达式、for 语句和过滤表达式；禁止多重 for 语句和多层过滤；情况复杂时应该用循环。

```python
# 正确:
result = [mapping_expr for value in iterable if filter_expr]

result = [complicated_transform(x)
          for x in iterable if predicate(x)]

descriptive_name = [
    transform({'key': key, 'value': value}, color='black')
    for key, value in generate_iterable(some_input)
    if complicated_condition_is_met(key, value)
]

result = []
for x in range(10):
    for y in range(5):
        if x * y > 10:
            result.append((x, y))

squares_generator = (x**2 for x in range(10))

unique_names = {user.name for user in users if user is not None}

# 错误:
result = [(x, y) for x in range(10) for y in range(5) if x * y > 10]

return ((x, y, z)
        for x in xrange(5)
        for y in xrange(5)
        if x != y
        for z in xrange(5)
        if y != z)
```

### 默认迭代器和操作符

只要可行，就用列表、字典和文件等类型的默认迭代器和操作符（容器类型具有默认的迭代器和关系运算符 `in` / `not in`）。默认迭代器和操作符简单高效，直白地表达运算，无需调用额外的函数；使用默认操作符的函数是泛型函数，可以用于任何支持该操作符的类型。

只要是支持的类型（例如列表、字典和文件），就使用默认迭代器和操作符。内置类型定义了一些返回迭代器的方法，优先使用返回迭代器的方法而非返回列表的方法，不过注意使用迭代器时不能修改容器。

```python
# 正确:
for key in adict: ...
if obj in alist: ...
for line in afile: ...
for k, v in adict.items(): ...

# 错误:
for key in adict.keys(): ...
for line in afile.readlines(): ...
```

### 生成器：按需使用

生成器函数返回一个迭代器，每当执行 `yield` 语句时生成一个值，随后运行状态暂停直到需要下一个值。代码简单（可保存局部变量和控制流），相较于直接创建整个列表的函数使用内存更少；但必须等到生成结束或者生成器本身被内存回收时，局部变量才能被回收。

可以使用。生成器的文档字符串中应使用 "Yields:" 而不是 "Returns:"。如果生成器占用了大量资源，一定要强制清理资源——一种好方法是用上下文管理器包裹生成器（PEP-0533）。

### Lambda 函数：适用于单行函数

lambda 定义匿名函数，方便，但比局部函数更难理解和调试——缺失函数名会导致调用栈晦涩难懂，且只能包含一个表达式、表达能力有限。

适用于单行函数；如果函数体超过 60-80 个字符，最好还是定义为常规的嵌套函数。建议用生成式替代 `map()/filter()` 与 `lambda` 的组合。对于乘法等常见操作，应该用 `operator` 模块中的函数代替 lambda 函数，例如推荐用 `operator.mul` 代替 `lambda x, y: x * y`。

### 条件表达式：适用于简单情况

条件表达式（三元运算符）是 if 语句的缩略版，例如 `x = 1 if cond else 2`；比 if 语句更简短方便，但有时比 if 语句更难理解——表达式很长时难以一眼望到条件。

适用于简单情况：以下每部分均不得长于一行——真值分支、if 部分和 else 部分；情况复杂时应使用完整的 if 语句。不要在 `else` 前换行处断开（`('yes' if predicate(value) else` 换行 `'no')` 是换行位置错误的反例）。

### 默认参数值：不能是可变对象

可以为参数列表的最后几个参数赋予默认值（`def foo(a, b = 0):`），轻松"仿造"重载行为。但默认参数在模块被导入时求值且只计算一次，如果值是列表和字典等可变类型，函数修改了这个值就可能引发问题。

可以使用，不过函数和方法的默认值不能是可变 (mutable) 对象：

```python
# 正确:
def foo(a, b=None):
    if b is None:
        b = []
def foo(a, b: Optional[Sequence] = None):
    if b is None:
        b = []
def foo(a, b: Sequence = ()):  # 允许空元组，因为元组是不可变的

# 错误:
def foo(a, b=[]):
    ...
def foo(a, b=time.time()):  # 确定要用模块的导入时间吗???
    ...
def foo(a, b=_FOO.value):  # 此时还没有解析 sys.argv...
    ...
def foo(a, b: Mapping = {}):  # 可能会赋值给未经过静态检查 (unchecked) 的代码
    ...
```

### 特性 (properties)：可用于简单计算与逻辑

可以用特性来读取或设置涉及简单计算、逻辑的属性；特性的实现必须和属性 (attribute) 一样满足这些通用要求：轻量、直白、明确。特性可以直接实现属性的访问、赋值接口而不必添加获取器和设置器，可以让属性变为只读，可以实现惰性求值，可以在类的内部实现变化时保持公开接口不变；但可能掩盖副作用（类似运算符重载），子类继承时可能产生困惑。

只能在必要时使用，并且要模仿常规属性的存取特点。一个特性不能仅仅用于获取和设置一个内部属性（因为不涉及计算，应该把该属性设为公有）；用特性来限制属性的访问或者计算**简单**的衍生值则是正确的。应该用 `@property` 装饰器来创建特性，自行实现的特性装饰器属于威力过大的功能。特性的继承机制难以理解，不要用特性实现子类能覆写（override）或扩展的计算功能。

### 访问器 (getter) 和设置器 (setter)

在访问和设置变量值时，如果访问器和设置器（又名为 accessor 和 mutator）可以产生有意义的作用或效果，则可以使用。特别来说，如果在当下或者可以预见的未来，读写某个变量的过程很复杂或者成本高昂，则应该使用这种函数。

如果一对访问器和设置器仅仅用于读写一个内部属性，你应该直接用公有属性取代它们。如果设置操作会让部分状态无效化或引发重建，则需要使用设置器——显式的函数调用表示可能出现特殊的操作。如果只有简单的逻辑，或者在重构代码后不再需要访问器和设置器，可以用属性 (property) 替代。访问器和设置器应该遵守命名规范，例如 `get_foo()` 和 `set_foo()`。

如果之前的代码通过属性获取数据，则不能把重新编写的访问器/设置器与这一属性绑定；应该让任何用老办法访问变量的代码出现显眼的错误，让使用者意识到代码复杂度有变化。

### True/False 的求值：尽可能使用"隐式"假值

Python 在计算布尔值时会把一些值视为 `False`：所有的"空"值都是假值，`0, None, [], {}, ""` 作为布尔值使用时相当于 `False`。隐式假值让条件语句更易懂、减少失误，多数时候运行速度也更快。尽可能使用"隐式"假值，例如使用 `if foo:` 而非 `if foo != []:`。注意事项：

1. 一定要用 `if foo is None:`（或者 `is not None`）来检测 `None` 值。例如检查某个默认值为 `None` 的参数有没有被调用者覆盖时，覆盖的值在布尔语义下可能也是假值。
2. 永远不要用 `==` 比较一个布尔值是否等于 `False`，应该用 `if not x:` 代替。如果需要区分 `False` 和 `None`，用复合表达式，例如 `if not x and x is not None:`。
3. 多利用空序列（字符串、列表、元组）是假值的特点：`if not seq:` 比 `if len(seq):` 和 `if not len(seq):` 更好。
4. 处理整数时，使用隐式 False 可能会得不偿失（例如不小心将 `None` 当做 0 来处理），可以显式比较整型值与 0 的关系（`len()` 的返回值例外）。
5. 注意 `'0'`（字符串，不是整数）作为布尔值时等于 `True`。
6. 注意把 Numpy 数组转换为布尔值时可能抛出异常，建议用 `.size` 属性检查 `np.array` 是否为空（例如 `if not users.size`）。

```python
# 正确:
if not users:
    print('无用户')

if i % 10 == 0:
    self.handle_multiple_of_ten()

def f(x=None):
    if x is None:
        x = []

# 错误:
if len(users) == 0:
    print '无用户'

if not i % 10:
    self.handle_multiple_of_ten()

def f(x=None):
    x = x or []
```
