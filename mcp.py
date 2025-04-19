#!/usr/bin/env python3
from base64 import b64decode
import mimetypes
import traceback
from sseclient import SSEClient
from urllib.parse import urljoin
from pathlib import Path
import requests
import argparse
import tempfile
import json


class MCP:
    def __init__(self, host: str):
        print(f"=== {host} ===")
        self.host = host
        self.sse = SSEClient(host + '/sse', timeout=5)
        response = next(self.sse)
        assert response.event == "endpoint", response.dump()
        self.messages_url = urljoin(self.host, response.data)

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
        print("Name:", repr(response["serverInfo"]["name"]))

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
            data = json.loads(next(self.sse).data)
            if 'error' in data:
                raise ValueError(data["error"])
            return data["result"]

    def list_tools(self):
        """
        https://spec.modelcontextprotocol.io/specification/2024-11-05/server/tools/#listing-tools
        """
        return self.jsonrpc("tools/list")["tools"]

    def list_resources(self):
        """
        https://modelcontextprotocol.io/specification/2024-11-05/server/resources#listing-resources
        """
        return self.jsonrpc("resources/list")["resources"] + \
            self.jsonrpc("resources/templates/list")["resourceTemplates"]

    # TODO: https://modelcontextprotocol.io/specification/2025-03-26/server/prompts

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

    def tool_call_example(arguments, result=None):
        if not "type" in arguments:
            if "anyOf" in arguments:
                return MCP.tool_call_example(arguments["anyOf"][0])
            else:
                return None

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

    args = parser.parse_args()

    if args.file:
        hosts = args.file.read_text().splitlines()
    else:
        hosts = [args.host]

    for host in hosts:
        if not host.startswith("http"):
            host = "http://" + host

        if not args.name_or_uri:
            # List tools
            results = []
            try:
                mcp = MCP(host)
                tools = mcp.list_tools()
                resources = mcp.list_resources()
                print("-> Tools:")
                if args.raw:
                    print(json.dumps(tools, indent=4))
                else:
                    for i, tool in enumerate(tools, 1):
                        example = MCP.tool_call_example(tool['inputSchema'])
                        command = f"{tool['name']} '{json.dumps(example)}'"
                        line1 = tool.get('description', command)
                        line2 = command if 'description' in tool else None
                        print(f" {i:>3}. {line1}")
                        if line2 is not None:
                            print(f"      {line2}")
                print("-> Resources:")
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
                results.append({
                    "host": host,
                    "success": True,
                    "tools": tools
                })
            except Exception as e:
                traceback.print_exc()
                results.append({
                    "host": host,
                    "success": False,
                    "error": str(e)
                })
            print()

            if args.output:
                json.dump(results, args.output.open("w"), indent=4)
                print(f"Results written to {args.output}")
        else:
            mcp = MCP(host)
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

                if args.output:
                    with args.output.open("w") as f:
                        json.dump(result, f, indent=4)
                    print(f"Results written to {args.output}")
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
                            print(f"{content['text']}")
                        elif content["type"] == "image" or content["type"] == "audio":
                            extension = mimetypes.guess_extension(
                                content["mimeType"])
                            with open(tempfile.mktemp(suffix=extension), "wb") as f:
                                f.write(b64decode(content["data"]))
                            print(f"File content saved to {f.name}")
                if args.output:
                    with args.output.open("w") as f:
                        json.dump(result, f, indent=4)
                    print(f"Results written to {args.output}")
