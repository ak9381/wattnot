"""A LangChain chat model behind WattNot. The only change: base_url."""
import os

from langchain_openai import ChatOpenAI

BASE_URL = os.environ.get("WATTNOT_BASE_URL", "http://127.0.0.1:8080/v1")


def run(base_url: str = BASE_URL, question: str = "Where do the signed receipts live?") -> str:
    llm = ChatOpenAI(base_url=base_url, api_key=os.environ.get("WATTNOT_KEY", "unused"),
                     model="meta-llama/Llama-3.1-8B-Instruct",
                     default_headers={"X-WattNot-Framework": "langchain"})
    return llm.invoke([("system", "You are a repo agent."), ("user", question)]).content


if __name__ == "__main__":
    print(run())
