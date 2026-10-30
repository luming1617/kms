## 七、字符串、日志、错误信息与资源

> 来源：Google Python 风格指南（中文版）「Python风格规范：字符串（含日志、错误信息小节）/ 文件、套接字和类似的有状态资源」。

### 字符串格式化

应该用 f-string、`%` 运算符或 `format` 方法来格式化字符串，即使所有参数都是字符串。你可以自行评判合适的选项；可以用 `+` 实现单次拼接，但是不要用 `+` 实现格式化。

```python
# 正确:
x = f'名称: {name}; 分数: {n}'
x = '%s, %s!' % (imperative, expletive)
x = '{}, {}'.format(first, second)
x = '名称: %s; 分数: %d' % (name, n)
x = '名称: %(name)s; 分数: %(score)d' % {'name':name, 'score':n}
x = '名称: {}; 分数: {}'.format(name, n)
x = a + b

# 错误:
x = first + ', ' + second
x = '名称: ' + name + '; 分数: ' + str(n)
```

不要在循环中用 `+` 和 `+=` 操作符来堆积字符串：这有时会产生平方而不是线性的时间复杂度；有时 CPython 会优化这种情况，但这是一种实现细节，无法轻易预测优化是否生效，未来情况可能出现变化。作为替代，将每个子串加入列表、在循环结束后用 `''.join` 拼接列表，或将每个子串写入一个 `io.StringIO` 缓冲区——这些技巧保证始终有线性的平摊 (amortized) 时间复杂度。

```python
# 正确:
items = ['<table>']
for last_name, first_name in employee_list:
    items.append('<tr><td>%s, %s</td></tr>' % (last_name, first_name))
items.append('</table>')
employee_table = ''.join(items)

# 错误:
employee_table = '<table>'
for last_name, first_name in employee_list:
    employee_table += '<tr><td>%s, %s</td></tr>' % (last_name, first_name)
employee_table += '</table>'
```

### 引号

保持同一文件中字符串引号的一致性：选择 `'` 或者 `"` 以后不要改变主意；如果需要避免用反斜杠来转义引号，则可以使用另一种引号。

多行字符串推荐使用 `"""` 而非 `'''`；当且仅当项目中用 `'` 给常规字符串打引号时，才能在文档字符串以外的多行字符串上使用 `'''`；无论如何，文档字符串必须使用 `"""`。

多行字符串不会跟进代码其他部分的缩进。如果需要避免字符串中的额外空格，可以用多个单行字符串拼接，或者用 `textwrap.dedent()` 删除每行开头的空格：

```python
# 错误:
long_string = """这样很难看.
不要这样做.
"""

# 正确:
long_string = """如果你可以接受多余的空格,
    就可以这样."""

long_string = ("如果你不能接受多余的空格,\n" +
               "可以这样.")

import textwrap

long_string = textwrap.dedent("""\
  这样也行, 因为 textwrap.dedent()
  会删除每一行开头共有的空格.""")
```

注意：`textwrap.dedent("""\` 中的反斜杠没有违反显式续行的禁令——此时反斜杠用于在字符串字面量中对换行符转义。

### 日志

对于那些第一个参数是格式字符串（包含 `%` 占位符）的日志函数：一定要用字符串字面量（而非 f-string!）作为第一个参数，并用占位符的参数作为其他参数。有些日志的实现会收集未展开的格式字符串作为可搜索的项目；这样也可以免于渲染那些被设置为不用输出的消息。

```python
# 正确:
logger.info('TensorFlow 的版本是: %s', tf.__version__)
logging.info('当前的 $PAGER 是: %s', os.getenv('PAGER', default=''))
logging.error('无法写入主目录, $HOME=%r', homedir)

# 错误:
logging.info('当前的 $PAGER 是:')
logging.info(os.getenv('PAGER', default=''))
logging.error(f'无法写入主目录, $HOME={homedir!r}')
```

### 错误信息

错误信息（例如诸如 `ValueError` 等异常的信息字符串和展示给用户的信息）应遵守三条规范：

1. 信息需要精确地匹配真正的错误条件。
2. 插入的片段一定要能清晰地分辨出来。
3. 要便于简单的自动化处理（例如正则搜索，也就是 grepping）。

```python
# 正确:
if not 0 <= p <= 1:
    raise ValueError(f'这不是概率值: {p!r}')

try:
    os.rmdir(workdir)
except OSError as error:
    logging.warning('无法删除这个文件夹 (原因: %r): %r',
                    error, workdir)

# 错误:
if p < 0 or p > 1:  # 问题: 遇到 float('nan') 时也为假!
    raise ValueError(f'这不是概率值: {p!r}')

try:
    os.rmdir(workdir)
except OSError:
    # 问题: 信息中存在错误的揣测，删除操作可能因为其他原因而失败,
    # 此时会误导调试人员.
    logging.warning('文件夹已被删除: %s', workdir)

try:
    os.rmdir(workdir)
except OSError:
    # 问题: 这个信息难以搜索, 而且某些 `workdir` 的值会让人困惑.
    logging.warning('无法删除%s文件夹.', workdir)
```

### 文件、套接字和类似的有状态资源

使用完文件和套接字以后，显式地关闭它们。这条规则也应该扩展到其他在内部使用套接字的可关闭资源（比如数据库连接）和其他需要用类似方法关停的资源，例子还有 `mmap` 映射、`h5py` 的文件对象和 `matplotlib.pyplot` 的图像窗口。

保持不必要的文件、套接字或其他有状态对象开启的缺点：
- 它们可能消耗有限的系统资源（例如文件描述符），代码需要使用大量类似资源而没有及时返还给系统时，可能出现原本可以避免的资源枯竭。
- 保持文件的开启状态会阻碍其他操作，例如移动、删除文件、卸载文件系统。
- 如果程序的多个部分共享文件和套接字，即使逻辑上文件已经关闭了，仍然有可能出现意外的读写操作；如果这些资源真正关闭了，读写操作会抛出异常，让问题早日浮出水面。

即使文件和套接字会在析构 (destruct) 时自动关闭，把对象的生命周期和资源状态绑定的行为依然不妥：无法保证运行时调用 `__del__` 方法的真正时机——不同的 Python 实现采用了不同的内存管理技巧（比如延迟垃圾处理机制），可能会随意、无限期地延长对象的生命周期；意想不到的文件引用（例如全局对象和异常的堆栈跟踪）可能让文件的存续时间比想象的更长。依赖于终结器 (finalizer) 实现自动清理的方法有显著的副作用，这在几十年里、在多种语言中多次引发严重问题。

推荐使用 `"with"` 语句管理文件和类似的资源：

```python
with open("hello.txt") as hello_file:
    for line in hello_file:
        print line
```

对于不支持 `with` 语句的类似文件的对象，应该使用 `contextlib.closing()`：

```python
import contextlib

with contextlib.closing(urllib.urlopen("https://www.python.org/")) as front_page:
    for line in front_page:
        print line
```

少数情况下无法基于上下文 (context) 管理资源，此时文档应该清楚地解释代码会如何管理资源的生命周期。
