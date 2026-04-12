import datetime
import os
import urllib
from openai import AsyncOpenAI
import json
import subprocess
import dotenv
import asyncio
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Grid, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Markdown
from textual.worker import Worker
from textual import events
from textual.message import Message
from textual.widgets import TextArea
import requests

dotenv.load_dotenv()


class PermissionDenied(Exception):
    pass


class UserObjection(Exception):
    pass

class ChatInput(TextArea):
    class Submitted(Message):
        def __init__(self, chat_input: "ChatInput", value: str) -> None:
            self.chat_input = chat_input
            self.value = value
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self, self.text))
            return

        if event.key in  ["shift+enter", "ctrl+j", "ctrl+enter"]:
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return

        await super()._on_key(event)

class PermissionScreen(ModalScreen):
    def __init__(self, prompt: str):
        super().__init__()
        self.prompt = prompt
        self.result = None

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(self.prompt, id="question"),
            Button("Allow", id="allow", variant="success"),
            Button("Deny", id="deny", variant="error"),
            Input(placeholder="Objection clarification", id="objection_input"),
            id="dialog",
        )

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "allow":
            self.result = "Allow"
        elif event.button.id == "deny":
            self.result = "Deny"
        else:
            self.result = "Provide Input"
        self.dismiss(self.result)

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted):
        self.result = event.value
        event.stop()
        self.dismiss(self.result)


class LikeAnAgent(App):
    CSS_PATH = "likeanagent.tcss"

    def __init__(self):
        super().__init__()
        self._event = None
        self._response = None
        self._prompt = None
        self.usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self.client = AsyncOpenAI(
            base_url=os.environ["OPENAI_HOST"], api_key=os.environ["OPENAI_KEY"]
        )
        self.tools = {
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
                "function": self.read_file,
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
                "function": self.write_file,
            },
            "patch_file": {
                "tool": {
                    "type": "function",
                    "description": "Apply a unified diff patch to a file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "The path to the file to patch. The user will be asked for permission before applying the patch.",
                            },
                            "diff": {
                                "type": "string",
                                "description": "The unified diff to apply to the file.",
                            },
                        },
                        "required": ["file_path", "diff"],
                        "additionalProperties": False,
                    },
                },
                "function": self.patch,
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
                "function": self.bash,
            },
            "web_search": {
                "tool": {
                    "type": "function",
                    "description": "Perform a web search using DuckDuckGo. The user will be asked for permission before performing the search.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query.",
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
                "function": self.web_search,
            },
            "get_date_time": {
                "tool": {
                    "type": "function",
                    "description": "Get the current date and time.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
                "function": lambda: json.dumps({"date_time": datetime.datetime.now().isoformat()}),
            }
        }

        self.llm_tools = [{**v["tool"], "name": k} for k, v in self.tools.items()]
        self.input_list = [
            {
                "role": "system",
                "content": f"""You are a helpful and cautious AI assistant with access to file system operations and shell commands. Your primary goal is to assist the user effectively while prioritizing safety and clarity.

The current date and time is {datetime.datetime.now().isoformat()}.

Key principles:
1. Always explain what you plan to do before performing actions that require permission
2. Be transparent about your reasoning and thought process
3. When uncertain, ask clarifying questions rather than making assumptions
4. Use the available tools (read_file, write_file, patch_file, bash) judiciously to help the user
5. Respect user decisions when they decline permission for an action
6. When modifying files, prefer using unified diffs/patches to minimize unintended changes and make it clear what will be modified. Only use full rewrites when absolutely necessary, and explain why.
7. You can fetch followup information for the web search using curl
8. If a user is asking a generic question, assume they refer to the present time, {datetime.datetime.now().isoformat()}.

Your capabilities:
- Read and write files in the current directory
- Apply unified diff patches to files
- Execute shell commands (with user permission)
- Get current date and time
- Perform web searches (with user permission)
- Maintain context across conversations
- Help with tasks like organizing notes, managing lists, and automating simple file operations


When responding:
- Be concise but thorough
- Use natural language explanations for your actions
- When suggesting file modifications, explain whether you'll use a full rewrite or a diff/patch approach
- Explain the benefits of your chosen approach (e.g., "using a diff will only change the specific lines needed")
- Acknowledge when you've completed a task successfully
- If something goes wrong, explain what happened and suggest alternatives

Remember: You're designed to be helpful while giving the user full control over potentially risky operations.""",
            },
        ]

    async def ask_permission(self, prompt: str):
        screen = PermissionScreen(prompt)
        result = await self.push_screen_wait(screen)
        if result == "Allow":
            return True
        elif result == "Deny":
            raise PermissionDenied()
        else:
            raise UserObjection(result)

    async def read_file(self, file_path: str):
        try:
            with open(file_path, "r") as f:
                return f.read()
        except FileNotFoundError:
            return "File not found"

    async def write_file(self, file_path: str, content: str):
        try:
            await self.ask_permission(f"Do you want to write to {file_path}?")
        except PermissionDenied:
            return "The user didn't allow to modify this file"
        except UserObjection as e:
            return f"The user objected: {str(e)}"

        with open(file_path, "w") as f:
            f.write(content)
        return "ok"

    async def bash(self, command: str):
        try:
            await self.ask_permission(f"Do you want to execute the command: {command}?")
        except PermissionDenied:
            return "The user didn't allow to execute this command"
        except UserObjection as e:
            return f"The user objected: {str(e)}"
        try:
            res = await asyncio.to_thread(
                subprocess.run, command, shell=True, capture_output=True, text=True
            )
            return json.dumps(
                {
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                    "returncode": res.returncode,
                }
            )
        except Exception as e:
            return str(e)

    async def patch(self, file_path: str, diff: str):
        try:
            await self.ask_permission(
                f"Do you want to apply the following patch to {file_path}?\n\n{diff}"
            )
        except PermissionDenied:
            return "The user didn't allow to modify this file"
        except UserObjection as e:
            return f"The user objected: {str(e)}"

        import tempfile
        import os

        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(diff)
            tmp_path = tmp.name

        try:
            res = await asyncio.to_thread(
                subprocess.run,
                f"patch {file_path} {tmp_path}",
                shell=True,
                capture_output=True,
                text=True,
            )
            return json.dumps(
                {
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                    "returncode": res.returncode,
                }
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    
    async def web_search(self, query: str):
        try:
            await self.ask_permission(f"Do you want to perform a web search for: {query}?")
        except PermissionDenied:
            return "The user didn't allow to perform a web search"
        except UserObjection as e:
            return f"The user objected: {str(e)}"
        try:
            searxng_url = os.environ["SEARXNG_URL"]
            response = requests.get(f"{searxng_url}/?q={urllib.parse.quote_plus(query)}&format=json")
        except Exception as e:
            return f"Failed to perform web search: {str(e)}"
        if response.status_code // 100 == 2:
            data = response.json()
            return json.dumps(data.get("results", []))
        else:
            return f"Web search failed with status code {response.status_code}"

    async def function_call(self, name: str, args: dict, call_id: str):
        ret = {
            "type": "function_call_output",
            "call_id": call_id,
        }
        print(f"Function call: {name} with args {args}")
        ret["output"] = (
            await self.tools[name]["function"](**args) or "ok"
            if name in self.tools
            else "unknown function"
        )
        print(
            f"Function call output: {ret['output'][:256]}{'...' if len(ret['output']) > 256 else ''}"
        )
        return ret

    async def async_request(self):
        event_types = {
            "function_call": "Function call",
            "message": "Message",
        }
        streamed_response = await self.client.responses.create(
            tools=self.llm_tools,
            input=self.input_list,
            temperature=0,
            stream=True,
        )
        def follow_chat() -> None:
            scroll = self.query_one(VerticalScroll)
            self.call_after_refresh(scroll.scroll_end, animate=False)
        markdown_widget = self.query(Markdown).last()
        stream = Markdown.get_stream(markdown_widget)
        ret = None
        async for event in streamed_response:
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
                follow_chat()
            elif event.type == "response.completed":
                ret = event.response
                pass
            elif event.type == "response.output_item.added":
                print(f"\n###{event}")
                await stream.write(
                    f"\n\n### {event_types.get(event.item.type, event.item.type)}\n"
                )
                # if event.item.type == 'function_call':
                #     await stream.write(f"|Name|Arguments|\n| -------- | ------- |\n|{event.item.name}|{event.item.arguments}|\n")
                await stream.write("\n\n")
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
        ret.output = [
            item.to_dict() if not isinstance(item, dict) else item
            for item in ret.output
        ]
        return ret

    @work(exclusive=True)
    async def request_loop(self):
        ready = False
        res = ""
        widget = self.query(Markdown).last()
        self.set_status("Processing...")
        while not ready:
            ready = True
            ret = await self.async_request()
            self.usage["input_tokens"] = ret.usage.input_tokens
            self.usage["output_tokens"] = ret.usage.output_tokens
            self.usage["total_tokens"] = ret.usage.total_tokens
            self.input_list += ret.output
            for item in ret.output:
                if item["type"] == "function_call":
                    await widget.append(
                        f"\n|Name|Arguments|\n| -------- | ------- |\n|{item['name']}|{item['arguments']}|\n"
                    )
                    function_output = await self.function_call(
                        item["name"], json.loads(item["arguments"]), item["call_id"]
                    )
                    await widget.append(
                        f"\nOutput:\n```\n{function_output['output'][:512]}{'...' if len(function_output['output']) > 512 else ''}\n```\n"
                    )
                    self.input_list.append(function_output)
                    ready = False
                elif item["type"] == "message":
                    res = item["content"][0]["text"]
                elif item["type"] in ["reasoning"]:
                    pass
                else:
                    print(f"Unknown output item type: {item['type']}")

        self.set_status(f"Tokens used: {self.usage['total_tokens']}")
        print(f"\nTokens used: {self.usage['total_tokens']}")
        return res

    def set_status(self, status: str):
        usage_label = self.query_one("#Status", Label)
        usage_label.update(status)

    def add_prompt(self, prompt: str):
        self.input_list.append(
            {
                "role": "user",
                "content": prompt,
            }
        )

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield VerticalScroll()
        self._prompt = ChatInput(id="main_input")
        self._prompt.focus()
        yield self._prompt
        yield Label("Status", id="Status")

    @on(ChatInput.Submitted)
    async def on_input_submitted(self, event: ChatInput.Submitted):
        prompt = event.value.strip()
        if not prompt:
            return
        scroll = self.query_one(VerticalScroll)
        scroll.mount(Label(prompt, classes="user_prompt"))
        scroll.mount(Markdown())
        self.add_prompt(prompt)
        self._prompt.clear()
        self._prompt.styles.height = 3
        self.request_loop()
        self._prompt.focus()
    
    @on(ChatInput.Changed)
    def on_chat_input_changed(self, event: ChatInput.Changed) -> None:
        text_area = event.text_area
        target_height = min(10, max(3, text_area.document.line_count + 2))
        text_area.styles.height = target_height

    def on_worker_state_changed(self, event: Worker.StateChanged):
        print(f"Worker state changed: {event.worker} is now {event.state}")


def main():
    agent = LikeAnAgent()
    agent.run()


if __name__ == "__main__":
    main()
