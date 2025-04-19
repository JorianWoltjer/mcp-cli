#!/usr/bin/env python3
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor, as_completed
import mimetypes
import traceback
from urllib.parse import urljoin
from pathlib import Path
import requests
import argparse
import tempfile
import json


class SSE:
    def __init__(self, url, **kwargs):
        self.url = url
        r = requests.get(url, stream=True, **kwargs)
        self.iter = r.iter_lines()

    def __next__(self):
        while True:
            line = next(self.iter).split(b": ", 1)
            if len(line) == 1:
                raise ValueError(f"Missing colon separator: {line!r}")
            name, value = line
            if name == b"" and "ping" in value:
                assert next(self.iter) == b""
                continue

            if name == b"event":
                event = value.decode("utf-8")
                name, value = next(self.iter).split(b": ", 1)
                assert name == b"data"
                data = value.decode("utf-8")
                assert next(self.iter) == b""
                return event, data


class MCP:
    def __init__(self, host: str, timeout: int = 10):
        self.host = host
        self.sse = SSE(host + '/sse', timeout=timeout)
        # self.sse.decoder = codecs.getincrementaldecoder(
        #     "utf-8")(errors='replace')
        event, data = next(self.sse)
        assert event == "endpoint", f"Received {(event, data)}"
        self.messages_url = urljoin(self.host, data)

        # https://spec.modelcontextprotocol.io/specification/2024-11-05/basic/lifecycle/#initialization
        response = self.jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {
                    "listChanged": True
                },
                "sampling": {}
            },
            "clientInfo": {
                "name": "ExampleClient",
                "version": "1.0.0"
            }
        })
        self.server_info = response["serverInfo"]

        self.jsonrpc("notifications/initialized", notification=True)

    def jsonrpc(self, method: str, params: dict = None, notification: bool = False) -> dict:
        """
        https://www.jsonrpc.org/specification
        """
        payload = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        if not notification:  # notifications are recognized by the absence of an ID
            payload["id"] = 1

        r = requests.post(self.messages_url, json=payload)
        assert r.ok, r.text

        if not notification:  # notifications don't have a response
            try:
                event, data = next(self.sse)
                data = json.loads(data)
            except json.JSONDecodeError as e:
                print("JSON:", repr(data))
                raise
            if 'error' in data:
                raise ValueError(data["error"])
            if 'result' not in data:
                raise ValueError(f"No result in {data}")
            return data["result"]

    def list_tools(self):
        """
        https://spec.modelcontextprotocol.io/specification/2024-11-05/server/tools/#listing-tools
        """
        try:
            return self.jsonrpc("tools/list")["tools"]
        except ValueError as e:
            if isinstance(e.args[0], dict) and e.args[0].get("code") == -32601:
                return []  # Server does not support tools
            raise

    def list_resource_templates(self):
        try:
            return self.jsonrpc("resources/templates/list")["resourceTemplates"]
        except ValueError as e:
            if isinstance(e.args[0], dict) and e.args[0].get("code") == -32601:
                return []  # Server does not support resource templates
            raise

    def list_resources(self):
        """
        https://modelcontextprotocol.io/specification/2024-11-05/server/resources#listing-resources
        """
        try:
            return self.jsonrpc("resources/list")["resources"] + self.list_resource_templates()
        except ValueError as e:
            if isinstance(e.args[0], dict) and e.args[0].get("code") == -32601:
                return []  # Server does not support resources
            raise

    def list_prompts(self):
        """
        https://modelcontextprotocol.io/specification/2025-03-26/server/prompts/#listing-prompts
        """
        try:
            return self.jsonrpc("prompts/list")["prompts"]
        except ValueError as e:
            if isinstance(e.args[0], dict) and e.args[0].get("code") == -32601:
                return []  # Server does not support resources
            raise

    def call_tool(self, name, arguments):
        """
        https://spec.modelcontextprotocol.io/specification/2024-11-05/server/tools/#calling-tools
        """
        return self.jsonrpc("tools/call", {
            "name": name,
            "arguments": arguments
        })["content"]

    def get_resource(self, uri):
        """
        https://modelcontextprotocol.io/specification/2024-11-05/server/resources/#getting-resources
        """
        return self.jsonrpc("resources/read", {
            "uri": uri
        })["contents"]

    def get_prompt(self, name, arguments):
        """
        https://modelcontextprotocol.io/specification/2025-03-26/server/prompts/#getting-prompts
        """
        return self.jsonrpc("prompts/get", {
            "name": name,
            "arguments": arguments
        })["messages"]

    def tool_call_example(arguments, result=None):
        if not "type" in arguments:
            if "anyOf" in arguments:
                return MCP.tool_call_example(arguments["anyOf"][0])
            else:
                return None
        if isinstance(arguments["type"], list):
            arguments["type"] = arguments["type"][0]

        if arguments["type"] == "string":
            result = ""
        elif arguments["type"] == "integer" or arguments["type"] == "number":
            result = 0
        elif arguments["type"] == "boolean":
            result = False
        elif arguments["type"] == "array":
            result = []
            if type(arguments["items"]) is dict:
                result.append(MCP.tool_call_example(arguments["items"]))
            else:
                for item in arguments["items"]:
                    result.append(MCP.tool_call_example(item))
        elif arguments["type"] == "object":
            result = {}
            if "properties" in arguments:
                for key, value in arguments["properties"].items():
                    if key not in arguments.get("required", []):
                        key += "?"
                    result[key] = MCP.tool_call_example(value)
        else:
            raise ValueError(f"Unsupported type: {arguments['type']}")

        return result


def get_mcp_info(host):
    """
    Get MCP server information.
    """
    try:
        mcp = MCP(host)
        tools = mcp.list_tools()
        resources = mcp.list_resources()
        prompts = mcp.list_prompts()
        return {
            "host": host,
            "server_info": mcp.server_info,
            "success": True,
            "tools": tools,
            "resources": resources,
            "prompts": prompts
        }
    except Exception as e:
        traceback.print_exc()
        return {
            "host": host,
            "success": False,
            "error": str(e)
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Interact with a Model Context Protocol server")

    hosts_group = parser.add_mutually_exclusive_group(required=True)
    hosts_group.add_argument("host", nargs='?', type=str,
                             help="The host of the MCP server")
    hosts_group.add_argument("-f", "--file", type=Path,
                             help="File containing newline-separated hosts")
    parser.add_argument("-o", "--output", type=Path,
                        help="Output JSON file for the results")
    parser.add_argument("name_or_uri", type=str, nargs='?',
                        help="The name of the tool/prompt to call, or a resource URI")
    parser.add_argument("args", nargs='?', type=str,
                        help="Arguments for the tool call in JSON format")
    parser.add_argument("-r", "--raw", action="store_true",
                        help="Print raw JSON response")
    parser.add_argument("-t", "--timeout", type=int, default=10,
                        help="Timeout for requests in seconds")
    parser.add_argument("-T", "--threads", type=int, default=10,
                        help="Number of threads to use for concurrent requests")

    args = parser.parse_args()

    if args.file:
        hosts = args.file.read_text().splitlines()
    else:
        hosts = [args.host]
    hosts = ["http://" +
             host if not host.startswith("http") else host for host in hosts]

    if not args.name_or_uri:
        # List tools/resources/prompts
        all_results = []
        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            future_to_host = {executor.submit(
                get_mcp_info, host): host for host in hosts}

            for future in as_completed(future_to_host):
                host = future_to_host[future]
                data = future.result()
                all_results.append(data)
                print(f"=== {host} ===")
                if not data["success"]:
                    print("Error:", data["error"])
                    continue

                print("Name:", repr(data["server_info"]["name"]))
                print("-> Tools:")
                tools = data["tools"]
                if args.raw:
                    print(json.dumps(tools, indent=4))
                else:
                    for i, tool in enumerate(tools, 1):
                        arguments = MCP.tool_call_example(tool['inputSchema'])
                        command = f"{tool['name']} '{json.dumps(arguments)}'"
                        line1 = tool.get('description', command)
                        line2 = command if 'description' in tool else None
                        print(f" {i:>3}. {line1}")
                        if line2 is not None:
                            print(f"      {line2}")
                print("-> Resources:")
                resources = data["resources"]
                if args.raw:
                    print(json.dumps(resources, indent=4))
                else:
                    for i, resource in enumerate(resources, 1):
                        if 'uriTemplate' in resource:
                            # Dynamic resource templates (callable)
                            command = f"'{resource['uriTemplate']}'"
                            line1 = resource.get('description', command)
                            line2 = command if 'description' in tool else None
                        else:
                            # Static resources
                            if resource['name'] != resource['uri']:
                                line1 = resource['name']
                                if 'description' in resource:
                                    line1 += f" ({resource['description']})"
                                line2 = resource['uri']
                            else:
                                if 'description' in resource:
                                    line1 = resource['description']
                                    line2 = resource['uri']
                                else:
                                    line1 = resource['uri']
                                    line2 = None

                        print(f" {i:>3}. {line1}")
                        if line2 is not None:
                            print(f"      {line2}")
                print("-> Prompts:")
                prompts = data["prompts"]
                if args.raw:
                    print(json.dumps(prompts, indent=4))
                else:
                    for i, prompt in enumerate(prompts, 1):
                        arguments = {
                            arg['name']: "" for arg in prompt.get('arguments', [])}
                        command = f"prompt/{prompt['name']} '{json.dumps(arguments)}'"
                        line1 = prompt["description"] if prompt.get(
                            'description') else command
                        line2 = command if prompt.get("description") else None
                        print(f" {i:>3}. {line1}")
                        if line2 is not None:
                            print(f"      {line2}")
                print()

        if args.output:
            with args.output.open("w") as f:
                json.dump(all_results, f, indent=4)
            print(f"Results written to {args.output}")
    else:
        # Call tool/prompt/resource
        mcp = MCP(args.host, timeout=args.timeout)
        if "://" in args.name_or_uri:
            # Fetch resource
            result = mcp.get_resource(args.name_or_uri)
            print()
            if args.raw:
                print("-> Result:")
                print(json.dumps(result, indent=4))
            else:
                print("-> Result:")
                for content in result:
                    extension = mimetypes.guess_extension(
                        content["mimeType"])
                    if 'blob' in content:
                        with open(tempfile.mktemp(suffix=extension), "wb") as f:
                            data = content.get(
                                "text", b64decode(content["blob"]))
                            f.write(b64decode(data))
                        print(f"File content saved to {f.name}")
                    elif "text" in content:
                        print(f"{content['text']}")
                    else:
                        raise ValueError(
                            f"Unsupported resource: {content}")
        elif args.name_or_uri.startswith("prompt/"):
            # Get prompt
            result = mcp.get_prompt(args.name_or_uri[7:],
                                    json.loads(args.args or "{}"))
            print()
            if args.raw:
                print("-> Result:")
                print(json.dumps(result, indent=4))
            else:
                print("-> Result:")
                for message in result:
                    content = message["content"]
                    if content["type"] == "text":
                        content_s = content["text"]
                    elif content["type"] == "image" or content["type"] == "audio":
                        extension = mimetypes.guess_extension(
                            content["mimeType"])
                        with open(tempfile.mktemp(suffix=extension), "wb") as f:
                            f.write(b64decode(content["data"]))
                        content_s = f"<{f.name}>"
                    elif content["type"] == "resource":
                        resource = content["resource"]
                        if 'text' in resource:
                            content_s = resource["text"]
                        elif 'blob' in resource:
                            extension = mimetypes.guess_extension(
                                resource["mimeType"])
                            with open(tempfile.mktemp(suffix=extension), "wb") as f:
                                data = resource.get(
                                    "text", b64decode(resource["blob"]))
                                f.write(b64decode(data))
                            content_s = f"<{f.name}>"
                        else:
                            content_s = f"<{resource['uri']}>"
                    else:
                        raise NotImplementedError
                    print(f"{message['role']}: {content_s}")
        else:
            # Call tool
            result = mcp.call_tool(args.name_or_uri,
                                   json.loads(args.args or "{}"))
            print()
            if args.raw:
                print("-> Result:")
                print(json.dumps(result, indent=4))
            else:
                print("-> Result:")
                for content in result:
                    if content["type"] == "text":
                        print(content['text'])
                    elif content["type"] == "image" or content["type"] == "audio":
                        extension = mimetypes.guess_extension(
                            content["mimeType"])
                        with open(tempfile.mktemp(suffix=extension), "wb") as f:
                            f.write(b64decode(content["data"]))
                        print(f"File content saved to {f.name}")
                    elif content["type"] == "resource":
                        resource = content["resource"]
                        if 'text' in resource:
                            print(resource["text"])
                        elif 'blob' in resource:
                            extension = mimetypes.guess_extension(
                                resource["mimeType"])
                            with open(tempfile.mktemp(suffix=extension), "wb") as f:
                                data = resource.get(
                                    "text", b64decode(resource["blob"]))
                                f.write(b64decode(data))
                            print(f"Resource content saved to {f.name}")
                        else:
                            print("Resource:", resource['uri'])
                    else:
                        raise NotImplementedError

            if args.output:
                with args.output.open("w") as f:
                    json.dump(result, f, indent=4)
                print(f"Result written to {args.output}")
