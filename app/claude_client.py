import asyncio
import json
import logging
from dataclasses import dataclass, field

import anthropic

from app.notebooklm_client import NotebookLMWrapper, Answer, Source

logger = logging.getLogger(__name__)

TOOLS = [
    {
        "name": "list_notebook_sources",
        "description": (
            "List all source documents available in the NotebookLM notebook. "
            "Returns a list of sources with their IDs and titles. "
            "Call this first to see what documents are available before reading them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "notebook_id": {
                    "type": "string",
                    "description": "The NotebookLM notebook ID to list sources from",
                }
            },
            "required": ["notebook_id"],
        },
    },
    {
        "name": "get_source_overview",
        "description": (
            "Get a document's total character count plus its BEGINNING (~first "
            "40,000 chars — usually the cover, table of contents, and early "
            "sections) and its END (~last 25,000 chars — usually the index or "
            "annexes). Use the table of contents and index to figure out WHERE "
            "in the document the answer lives before reading further."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string", "description": "The NotebookLM notebook ID"},
                "source_id": {"type": "string", "description": "The source document ID"},
            },
            "required": ["notebook_id", "source_id"],
        },
    },
    {
        "name": "read_source_section",
        "description": (
            "Read a targeted window of a source document starting at a "
            "character offset. To estimate an offset from the table of "
            "contents or index: a section that appears halfway through the "
            "document's structure is at roughly char_count * 0.5. After "
            "reading a window, check which section you actually landed in and "
            "adjust the offset up or down. Iterate a few targeted reads "
            "rather than reading the whole document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string", "description": "The NotebookLM notebook ID"},
                "source_id": {"type": "string", "description": "The source document ID"},
                "start_char": {
                    "type": "integer",
                    "description": "Character offset to start reading from (0-based)",
                },
                "length": {
                    "type": "integer",
                    "description": "How many characters to read (default 50000, max 80000)",
                },
            },
            "required": ["notebook_id", "source_id", "start_char"],
        },
    },
]

SYSTEM_PROMPT = """You are a helpful knowledge assistant. You answer questions by reading source documents from a NotebookLM notebook. The documents are often large reference books (codes, standards, manuals), so navigate them the way an expert would:

1. Call list_notebook_sources to see what documents are available.
2. For the most relevant document(s), call get_source_overview. The beginning usually holds the table of contents; the end usually holds the index. Study both to locate which section/chapter answers the question.
3. Estimate the character offset of that section (proportional position within the document) and call read_source_section there. Check what section you landed in and adjust the offset until you find the right material. Prefer a few targeted reads over reading everything.
4. Synthesize the answer strictly from what you actually read.
5. Always cite the document title and the section number, and quote the relevant passages.
6. If you cannot find the answer in the sources, say so clearly — never fill gaps with general knowledge.

Format citations like: [Source: "Document Title", Section X.Y]"""


class ClaudeFallbackClient:
    def __init__(self, api_key: str, model: str, nlm_client: NotebookLMWrapper):
        self._anthropic = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._nlm = nlm_client

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> str:
        try:
            if tool_name == "list_notebook_sources":
                sources = await self._nlm.list_sources(tool_input["notebook_id"])
                return json.dumps(
                    [{"source_id": s.source_id, "title": s.title} for s in sources]
                )
            elif tool_name == "get_source_overview":
                ov = await self._nlm.get_source_overview(
                    tool_input["notebook_id"], tool_input["source_id"]
                )
                if not ov["head"]:
                    return "No content available for this source."
                parts = [
                    f"Document length: {ov['char_count']} characters.",
                    f"=== BEGINNING (chars 0-{len(ov['head'])}) ===\n{ov['head']}",
                ]
                if ov["tail"]:
                    parts.append(
                        f"=== END (chars {ov['char_count'] - len(ov['tail'])}-"
                        f"{ov['char_count']}) ===\n{ov['tail']}"
                    )
                else:
                    parts.append("(The document fits entirely in the excerpt above.)")
                return "\n\n".join(parts)
            elif tool_name == "read_source_section":
                window = await self._nlm.read_source_section(
                    tool_input["notebook_id"],
                    tool_input["source_id"],
                    int(tool_input["start_char"]),
                    int(tool_input.get("length", 50_000)),
                )
                if not window["content"]:
                    return "No content available at that offset."
                return (
                    f"Document length: {window['char_count']} characters. "
                    f"Showing chars {window['start_char']}-{window['end_char']}:\n\n"
                    f"{window['content']}"
                )
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error("Tool call %s failed: %s", tool_name, e)
            return f"Error accessing source: {e}"

    async def ask(self, notebook_id: str, question: str) -> Answer:
        messages = [
            {
                "role": "user",
                "content": (
                    f"The notebook ID is: {notebook_id}\n\n"
                    f"Question: {question}"
                ),
            }
        ]

        # Navigating a large document takes several tool rounds: overview, a
        # few targeted section reads (with offset adjustments), then the answer.
        max_rounds = 16
        for _ in range(max_rounds):
            response = await self._anthropic.messages.create(
                model=self._model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                answer_text = ""
                for block in response.content:
                    if block.type == "text":
                        answer_text += block.text
                return Answer(
                    text=answer_text,
                    citations=[],
                    sources=[],
                    engine="claude",
                )

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = await self._handle_tool_call(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                break

        return Answer(
            text="I wasn't able to fully process your question. Please try again.",
            engine="claude",
        )
