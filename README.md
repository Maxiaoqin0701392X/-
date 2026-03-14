# -
上位机python实现

---

## Python 串口通信基础

### 官方文档 / 推荐资源

#### 1. pyserial —— Python 串口通信标准库

- **官网 / 官方文档：** [https://pyserial.readthedocs.io/en/latest/](https://pyserial.readthedocs.io/en/latest/)
- **PyPI 主页：** [https://pypi.org/project/pyserial/](https://pypi.org/project/pyserial/)
- **GitHub 源码：** [https://github.com/pyserial/pyserial](https://github.com/pyserial/pyserial)

`pyserial` 是 Python 中使用最广泛的串口通信库，支持 Windows、Linux、macOS 及嵌入式平台。

**基本使用示例：**

```python
import serial

# 打开串口（波特率 115200，超时 1 秒）
ser = serial.Serial('/dev/ttyUSB0', baudrate=115200, timeout=1)

# 发送数据
ser.write(b'\xAA\x01\x02\x03\xBB')

# 接收数据
data = ser.read(5)   # 读取 5 个字节
print(data.hex())

ser.close()
```

#### 2. Python 标准库 `serial` / `io` 模块

- Python 官方文档 — `io` 模块：[https://docs.python.org/3/library/io.html](https://docs.python.org/3/library/io.html)

---

## 二进制协议解析

### 官方文档 / 推荐资源

#### 1. Python `struct` 模块 —— 二进制数据打包/解包

- **官方文档：** [https://docs.python.org/3/library/struct.html](https://docs.python.org/3/library/struct.html)

`struct` 是 Python 内置的用于解析二进制协议的标准库，无需安装额外依赖。

**基本使用示例：**

```python
import struct

# 解包：将字节流解析为 (uint8, uint16, float)
raw = b'\x01\x00\x0A\x00\x00\x80\x3F'
header, length, value = struct.unpack('<BHf', raw)
print(f"header={header}, length={length}, value={value}")

# 打包：将数据封装为字节流
packet = struct.pack('<BHf', 0x01, 10, 1.0)
print(packet.hex())
```

常用格式字符说明：

| 格式符 | C 类型        | Python 类型 | 字节数 |
|--------|---------------|-------------|--------|
| `B`    | unsigned char | int         | 1      |
| `H`    | unsigned short| int         | 2      |
| `I`    | unsigned int  | int         | 4      |
| `f`    | float         | float       | 4      |
| `d`    | double        | float       | 8      |

> 前缀 `<` 表示小端（little-endian），`>` 表示大端（big-endian）。

#### 2. `construct` 库 —— 声明式二进制协议解析

- **官网 / 官方文档：** [https://construct.readthedocs.io/en/latest/](https://construct.readthedocs.io/en/latest/)
- **GitHub 源码：** [https://github.com/construct/construct](https://github.com/construct/construct)
- **PyPI 主页：** [https://pypi.org/project/construct/](https://pypi.org/project/construct/)

`construct` 提供声明式的二进制协议描述方式，适合复杂帧格式的解析与封装。

#### 3. `bitstruct` 库 —— 位级二进制协议解析

- **官方文档 / PyPI：** [https://pypi.org/project/bitstruct/](https://pypi.org/project/bitstruct/)
- **GitHub 源码：** [https://github.com/eerimoq/bitstruct](https://github.com/eerimoq/bitstruct)

适用于需要按位（bit）操作的协议（如 CAN 总线、自定义嵌入式协议）。

---

## 相关参考论文 / 技术文章

1. **串口通信协议综述**
   - 《基于Python的串口通信设计与实现》—— 常见于中国知网（CNKI），搜索关键词：`Python 串口通信 上位机`
   - CNKI 检索链接：[https://www.cnki.net/](https://www.cnki.net/)

2. **二进制协议解析**
   - 《嵌入式系统中二进制通信协议的设计与解析》—— 搜索关键词：`二进制协议 解析 嵌入式`
   - 万方数据检索：[https://www.wanfangdata.com.cn/](https://www.wanfangdata.com.cn/)

3. **英文参考**
   - "Serial Communication with Python" — Real Python 教程：[https://realpython.com/python-pyserial/](https://realpython.com/python-pyserial/)
   - "Binary Data in Python" — Python Docs：[https://docs.python.org/3/library/stdtypes.html#bytes-objects](https://docs.python.org/3/library/stdtypes.html#bytes-objects)

---

## 快速安装

```bash
pip install pyserial
pip install construct   # 可选，用于复杂协议解析
pip install bitstruct   # 可选，用于位级协议解析
```
