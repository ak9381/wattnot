"""A LlamaIndex LLM behind WattNot. The only change: api_base."""
import os

from llama_index.core.llms import ChatMessage
from llama_index.llms.openai_like import OpenAILike

BASE_URL = os.environ.get("WATTNOT_BASE_URL", "http://127.0.0.1:8080/v1")


def run(base_url: str = BASE_URL, question: str = "Where do the signed receipts live?") -> str:
    llm = OpenAILike(api_base=base_url, api_key=os.environ.get("WATTNOT_KEY", "unused"),
                     model="meta-llama/Llama-3.1-8B-Instruct", is_chat_model=True,
                     default_headers={"X-WattNot-Framework": "llamaindex"})
    return llm.chat([ChatMessage(role="user", content=question)]).message.content


if __name__ == "__main__":
    print(run())
