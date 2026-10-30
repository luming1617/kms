## 八、异常与错误处理

> 来源：Google Python 风格指南（中文版）「Python语言规范：异常」；PEP 8（中文版）「编程建议」中与异常相关的条目。

### 允许使用异常，但必须谨慎使用

异常是一种跳出正常的控制流、以处理错误或其它异常情况的方法。它让处理正常情况的控制流不与错误处理代码混在一起，也能让控制流跳出多层调用帧（一步跳出多层嵌套的函数，而不必逐层传递错误代码）；但可能导致控制流晦涩难懂，调用库函数时容易忘记处理异常。使用异常时必须遵守以下要求。

### 优先使用合适的内置异常类；assert 不用于参数校验

用 `ValueError` 表示前置条件错误（例如给必须为正数的参数传入了负值）。不要使用 `assert` 语句来验证公开 API 的参数值：应该用 `assert` 来保证内部正确性，不应该用 `assert` 来纠正参数或表示意外情况；若要用异常来表示意外情况，应该用 `raise`。

```python
# 正确:
def connect_to_next_port(self, minimum: int) -> int:
    """连接到下一个可用的端口.

    参数:
        minimum: 一个大于等于 1024 的端口号.

    返回:
        新的最小端口.

    抛出:
        ConnectionError: 没有可用的端口.
    """
    if minimum < 1024:
        # 注意这里抛出 ValueError 的情况没有在文档里说明，因为 API 的
        # 错误用法应该是未定义行为.
        raise ValueError(f'最小端口号至少为 1024，不能是 {minimum}.')
    port = self._find_next_open_port(minimum)
    if port is None:
        raise ConnectionError(
            f'未能通过 {minimum} 或更高的端口号连接到服务.')
    assert port >= minimum, (
        f'意外的端口号 {port}, 端口号不应小于 {minimum}.')
    return port

# 错误:
def connect_to_next_port(self, minimum: int) -> int:
    """连接到下一个可用的端口.

    参数:
        minimum: 一个大于等于 1024 的端口号.

    返回:
        新的最小端口.
    """
    assert minimum >= 1024, '最小端口号至少为 1024.'
    port = self._find_next_open_port(minimum)
    assert port is not None
    return port
```

### 自定义异常（Google + PEP 8）

模块或包可以定义自己的异常类型，这些类必须继承已有的异常类。异常类型名应该以 `Error` 为后缀，并且不应该有重复（例如 `foo.FooError`）。

PEP 8：从 `Exception` 而不是 `BaseException` 派生异常，直接从 `BaseException` 继承保留用于那些几乎总是错误处理方式的异常。基于代码**捕获**异常可能需要的区别来设计异常层次结构，而不是基于引发异常的位置——目标是程序化地回答"哪里出错了？"，而不是仅仅声明"发生了问题"（PEP 3151 有关于内置异常层次结构的学习示例）。此处适用类命名约定，但如果异常是错误，应在异常类名后添加 "Error" 后缀；用于非局部流控制或其他信号形式的非错误异常无需特殊后缀。

### 禁止裸 except 捕获所有异常（Google）

永远不要使用 `except:` 语句来捕获所有异常，也不要捕获 `Exception` 或者 `StandardError`，除非你想：
1. 重新抛出异常；
2. 在程序中创造一个隔离点，记录并抑制异常，让异常不再继续传播（这种写法可以用在线程的最外层，以避免程序崩溃）。

如果你使用这种写法，Python 将非常宽容：`except:` 真的会捕获任何错误，包括拼写错误的符号名、`sys.exit()` 调用、`Ctrl+C` 中断、单元测试错误和各种你不想捕获的错误。

### 捕获异常时尽可能提及特定异常（PEP 8）

```python
try:
    import platform_specific_module
except ImportError:
    platform_specific_module = None
```

裸 `except:` 子句将捕获 `SystemExit` 和 `KeyboardInterrupt` 异常，使程序难以用 Control-C 中断，并可能掩盖其他问题。如果想捕获所有表示程序错误的异常，使用 `except Exception:`（裸 `except` 等同于 `except BaseException:`）。一个好的经验法则是将裸 `except` 子句的使用限制在两种情况：
- 如果异常处理程序将打印或记录回溯——至少用户会知道发生了错误。
- 如果代码需要做一些清理工作，然后让异常通过 `raise` 向上层传播（`try...finally` 可能是处理这种情况的更好方法）。

在捕获操作系统错误时，优先使用 Python 3.3 中引入的显式异常层次结构，而不是对 `errno` 值进行自省。

### 最小化 try 代码块

Google：最小化 `try/except` 代码块中的代码量——`try` 的范围越大，就越容易把你没想到的那些能抛出异常的代码囊括在内，`try/except` 代码块就掩盖了真正的错误。

PEP 8：对于所有 `try/except` 子句，将 `try` 子句限制在绝对最小的代码量，同样可以避免掩盖 bug：

```python
# 正确:
try:
    value = collection[key]
except KeyError:
    return key_not_found(key)
else:
    return handle_value(value)

# 错误:
try:
    # Too broad!
    return handle_value(collection[key])
except KeyError:
    # Will also catch KeyError raised by handle_value()
    return key_not_found(key)
```

### 用 finally 表示无条件执行的代码

用 `finally` 表示无论异常与否都应执行的代码，这种写法常用于清理资源，例如关闭文件（Google）。

在 `try...finally` 的 finally 套件中使用流控制语句 `return`/`break`/`continue`，并且流控制语句会跳出 finally 套件，这是不鼓励的——此类语句会隐式取消任何正在通过 finally 套件传播的活动异常（PEP 8）：

```python
# Wrong:
def foo():
    try:
        1 / 0
    finally:
        return 42
```

### 异常链（PEP 8）

适当使用异常链：应该使用 `raise X from Y` 来表示显式替换，而不会丢失原始回溯。当故意替换内部异常（使用 `raise X from None`）时，请确保相关细节已传输到新异常（例如在将 `KeyError` 转换为 `AttributeError` 时保留属性名称，或将原始异常的文本嵌入到新异常消息中）。
