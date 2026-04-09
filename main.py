import asyncio
import time
import os
from openai import OpenAI
import json
import subprocess
import dotenv
from textual import on
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Label, Markdown

dotenv.load_dotenv()

light_state = {
    "living_room": "off",
    "kitchen": "off",
}


class PermissionDenied(Exception):
    pass

class UserObjection(Exception):
    pass


def ask_permission(prompt: str):
    print(prompt)
    res = input("Allow? [q/y/N] ")
    if res.lower() == "q":
        user_response = input(">>> ")
        raise UserObjection(user_response)
    if res.lower() != "y":
        raise PermissionDenied("User denied permission")


def read_file(file_path: str):
    try:
        with open(file_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return "File not found"


def write_file(file_path: str, content: str):
    try:
        ask_permission(f"Do you want to write to {file_path}?")
    except PermissionDenied:
        return "The user didn't allow to modify this file"
    except UserObjection as e:
        return f"The user objected: {str(e)}"
    
    with open(file_path, "w") as f:
        f.write(content)
    return "ok"


def bash(command: str):
    try:
        ask_permission(f"Do you want to execute the command: {command}?")
    except PermissionDenied:
        return "The user didn't allow to execute this command"
    except UserObjection as e:
        return f"The user objected: {str(e)}"
    try:
        res = subprocess.run(command, shell=True, capture_output=True, text=True)
        return json.dumps(
            {
                "stdout": res.stdout,
                "stderr": res.stderr,
                "returncode": res.returncode,
            }
        )
    except Exception as e:
        return str(e)


tools = {
    "read_file": {
        "tool": {
            "type": "function",
            "description": "Read the content of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to read.",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        },
        "function": read_file,
    },
    "write_file": {
        "tool": {
            "type": "function",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to write. The user will be asked for permission before writing to the file.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file.",
                    },
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
        },
        "function": write_file,
    },
    "bash": {
        "tool": {
            "type": "function",
            "description": "Execute a bash command. The user will be asked for permission before executing the command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
        "function": bash,
    },
}

llm_tools = [{**v["tool"], "name": k} for k, v in tools.items()]


class MaybeAgent(App):
    def __init__(self):
        super().__init__()
        self.usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self.client = OpenAI(base_url=os.environ["OPENAI_HOST"], api_key=os.environ["OPENAI_KEY"])
        self.input_list = [
            {
                "role": "system",
                "content": """You are a helpful and cautious AI assistant with access to file system operations and shell commands. Your primary goal is to assist the user effectively while prioritizing safety and clarity.

Key principles:
1. Always explain what you plan to do before performing actions that require permission
2. Be transparent about your reasoning and thought process
3. When uncertain, ask clarifying questions rather than making assumptions
4. Use the available tools (read_file, write_file, bash) judiciously to help the user
5. Respect user decisions when they decline permission for an action

Your capabilities:
- Read and write files in the current directory
- Execute shell commands (with user permission)
- Maintain context across conversations
- Help with tasks like organizing notes, managing lists, and automating simple file operations

When responding:
- Be concise but thorough
- Use natural language explanations for your actions
- Acknowledge when you've completed a task successfully
- If something goes wrong, explain what happened and suggest alternatives

Remember: You're designed to be helpful while giving the user full control over potentially risky operations.""",
            },
        ]

    def function_call(self, name: str, args: dict, call_id: str):
        ret = {
            "type": "function_call_output",
            "call_id": call_id,
        }
        print(f"Function call: {name} with args {args}")
        ret["output"] = (
            tools[name]["function"](**args) or "ok"
            if name in tools
            else "unknown function"
        )
        print(f"Function call output: {ret['output'][:256]}{'...' if len(ret['output']) > 256 else ''}")
        return ret

    async def async_request(self):
        streamed_response = self.client.responses.create(
            tools=llm_tools,
            input=self.input_list,
            temperature=0,
            stream=True,
        )
        markdown_widget = self.query(Markdown).last()
        stream = Markdown.get_stream(markdown_widget)
        ret = None
        for event in streamed_response:
            if event.type == "response.created":
                pass
            elif event.type == "response.in_progress":
                pass
            elif event.type in [
                "response.reasoning_text.delta",
                "response.output_text.delta",
                "response.function_call_arguments.delta",
            ]:
                # print(event.delta, end="")
                await stream.write(event.delta)
            elif event.type == "response.completed":
                ret = event.response
                pass
            elif event.type == "response.output_item.added":
                # print(f"\n###{event.item.type}")
                await stream.write(f"\n###{event.item.type}\n")
            elif event.type in [
                "response.content_part.added",
                "response.content_part.done",
                "response.output_text.done",
                "response.output_item.done",
            ]:
                # print(event.type)
                pass
            else:
                # print(f"Unknown event type: {event.type}")
                await stream.write(f"\nUnknown event type: {event.type}\n")
        await stream.stop()
        return ret

    async def request_loop(self):
        ready = False
        res = ""
        while not ready:
            ready = True
            ret = await self.async_request()
            self.usage["input_tokens"] = ret.usage.input_tokens
            self.usage["output_tokens"] = ret.usage.output_tokens
            self.usage["total_tokens"] = ret.usage.total_tokens
            self.input_list += ret.output
            for item in ret.output:
                if item.type == "function_call":
                    self.input_list.append(
                        self.function_call(
                            item.name, json.loads(item.arguments), item.call_id
                        )
                    )
                    ready = False
                elif item.type == "message":
                    res = item.content[0].text
                elif item.type in ["reasoning"]:
                    pass
                else:
                    print(f"Unknown output item type: {item.type}")
        print(f"\nTokens used: {self.usage['total_tokens']}")
        return res

    def add_prompt(self, prompt: str):
        self.input_list.append(
            {
                "role": "user",
                "content": prompt,
            }
        )

    def main_loop(self):
        while True:
            try:
                user_input = input(">>> ")
            except KeyboardInterrupt:
                print("Bye")
                break
            self.add_prompt(user_input)
            self.request_loop()
    
    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield VerticalScroll(Input())
    
    @on(Input.Submitted)
    async def on_input_submitted(self, event: Input.Submitted):
        input = self.query_one(Input)
        input.remove()
        scroll = self.query_one(VerticalScroll)
        scroll.mount(Label(input.value))
        scroll.mount(Markdown())
        self.add_prompt(input.value)
        await self.request_loop()
        scroll.mount(Input())


def main():
    agent = MaybeAgent()
    agent.run()


if __name__ == "__main__":
    main()
