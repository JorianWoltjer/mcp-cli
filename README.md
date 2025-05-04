# MCP CLI

This is a simple MCP ([Model Context Protocol](https://modelcontextprotocol.io/introduction)) CLI that interacts with SSE (Server-Sent Events) transport mode. You can **list tools/resources/prompts**, as well as **call/fetch** them. This is useful for checking what an LLM would see if they want to interact with it, and also if you find an unknown server somewhere and want to figure out what it does.

## Install

```sh
git clone https://github.com/JorianWoltjer/mcp-cli.git && cd mcp-cli
python3 -m pip install requests
sudo ln -s $(pwd)/mcpc.py /usr/bin/mcpc
```

## Example Usage

You can host a simple example server by following the [SDK's Quickstart guide](https://github.com/modelcontextprotocol/python-sdk?tab=readme-ov-file#quickstart), then adding the following to the `server.py`:

```py
mcp.settings.port = 8080
mcp.run(transport="sse")
```

You can then interact with this server using `mcpc` by providing its address as the first positional argument:

```shell
$ mcpc localhost:8080
=== http://localhost:8080 ===
Name: 'Demo'
-> Tools:
   1. Add two numbers
      add '{"a": 0, "b": 0}'
-> Resources:
-> Prompts:
```

Here, we can see a single tool with a description of "Add two numbers". Below, there is always a generated example that you can append to the listing command in order to call it however you like:

```shell
$ mcpc localhost:8080 add '{"a": 1, "b": 2}'
3
```

As you can see, the second positional argument is the *tool name*, and the third is its arguments formatted in JSON. These arguments can include optional keys signified by a `?` suffix that you should remove if you actually want to use it.

In rare cases, the arguments specification is so complex with references that this tool cannot correctly recognize it to generate an example. In these cases, try using `-r` (`--raw`) to get the raw JSON output and help figure out what the server wants yourself.

Apart from tools, you can also **request resources or prompts**. These should be put as the second positional argument and may contain placeholders:

```shell
$ mcpc localhost:8080
=== http://localhost:8080 ===
Name: 'Demo'
-> Tools:
-> Resources:
   1. Read a file
      'file:///{name}'
-> Prompts:
   1. Create an introduction message
      prompt/greeting '{"name": ""}'
```

You can call these the same way as you would tools. Resources are recognized by `://` in their name, and prompts are prefixed with `prompt/`.

```shell
$ mcpc localhost:8080 'file:///server.py'
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Demo")

@mcp.resource("file:///{name}")
def source(name) -> str:
    """Read a file"""
    with open(name, "r") as f:
        return f.read()

@mcp.prompt()
def greeting(name) -> str:
    """Create an introduction message"""
    return f"Hello, I am {name}"

mcp.settings.port = 8080
mcp.run(transport="sse")

$ mcpc localhost:8080 prompt/greeting '{"name": "Jorian"}'
user: Hello, I am Jorian
```

Finally, you can pass a file with `-f` (`--file`) to **list** all tools **for each host** in a list of newline-separated URLs (without the `/sse` suffix). For example:

```
https://example.com
http://localhost:8000
http://localhost:8080
```

Because with many hosts, this will generate tons of output, you can use `-o` (`--output`) with a path to write results in JSON form to the specified file at the end:

```shell
$ mcpc -f list.txt -o output.json

=== http://localhost:8000 ===
Error: HTTPConnectionPool(host='localhost', port=8000): Max retries exceeded with url: /sse (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x7f84f7396dd0>: Failed to establish a new connection: [Errno 111] Connection refused'))

=== http://localhost:8080 ===
Name: 'Demo'
-> Tools:
   1. Add two numbers
      add '{"a": 0, "b": 0}'
-> Resources:
   1. Read a file
      'file:///{name}'
-> Prompts:
   1. Create an introduction message
      prompt/greeting '{"name": ""}'

=== https://example.com ===
Error: HTTPSConnectionPool(host='example.com', port=443): Read timed out. (read timeout=10)

Results written to output.json

$ jq -c . output.json
[{"host":"http://localhost:8000","success":false,"error":"HTTPConnectionPool(host='localhost', port=8000): Max retries exceeded with url: /sse (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x7f84f7396dd0>: Failed to establish a new connection: [Errno 111] Connection refused'))"},{"host":"http://localhost:8080","server_info":{"name":"Demo","version":"1.6.0"},"success":true,"tools":[{"name":"add","description":"Add two numbers","inputSchema":{"properties":{"a":{"title":"A","type":"integer"},"b":{"title":"B","type":"integer"}},"required":["a","b"],"title":"addArguments","type":"object"}}],"resources":[{"uriTemplate":"file:///{name}","name":"source","description":"Read a file"}],"prompts":[{"name":"greeting","description":"Create an introduction message","arguments":[{"name":"name","required":true}]}]},{"host":"https://example.com","success":false,"error":"HTTPSConnectionPool(host='example.com', port=443): Read timed out. (read timeout=10)"}]
```

By default, this uses a multi-threaded pool of 10 connections at the same time, but can be altered using `-T` (`--threads`). Also the `-t` (`--timeout`) with a default of 10 seconds can be increased/decreased in case a scan is taking too long, or connections are closed too fast.
You can then manually review the results for interesting tools, resources, prompts and arguments.
