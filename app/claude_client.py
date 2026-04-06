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
        "name": "read_source_content",
        "description": (
            "Read the full text content of a specific source document from the notebook. "
            "Use the source_id from list_notebook_sources to identify which source to read."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "notebook_id": {
                    "type": "string",
                    "description": "The NotebookLM notebook ID",
                },
                "source_id": {
                    "type": "string",
                    "description": "The ID of the specific source document to read",
                },
            },
            "required": ["notebook_id", "source_id"],
        },
    },
]

SYSTEM_PROMPT = """You are a helpful knowledge assistant. You answer questions by reading source documents from a NotebookLM notebook.

Instructions:
1. First, list the available sources in the notebook using list_notebook_sources.
2. Based on the question, read the most relevant source documents using read_source_content.
3. Synthesize an answer from the source content.
4. Always cite your sources by referencing the document title and quoting relevant passages.
5. If the sources don't contain enough information to answer the question, say so clearly.
6. Keep answers concise and focused on what the sources say.

Format citations like: [Source: "Document Title"]"""


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
            elif tool_name == "read_source_content":
                content = await self._nlm.get_source_content(
                    tool_input["notebook_id"], tool_input["source_id"]
                )
                return content if content else "No content available for this source."
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

        max_rounds = 10
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
