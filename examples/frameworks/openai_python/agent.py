"""Plain openai-python behind WattNot. The only change: base_url."""
import os

from openai import OpenAI

BASE_URL = os.environ.get("WATTNOT_BASE_URL", "http://127.0.0.1:8080/v1")


def run(base_url: str = BASE_URL, question: str = "Where do the signed receipts live?") -> str:
    client = OpenAI(base_url=base_url, api_key=os.environ.get("WATTNOT_KEY", "unused"),
                    default_headers={"X-WattNot-Framework": "openai-python"})
    out = client.chat.completions.create(
        model="meta-llama/Llama-3.1-8B-Instruct",
        messages=[{"role": "user", "content": question}])
    return out.choices[0].message.content


if __name__ == "__main__":
    print(run())
