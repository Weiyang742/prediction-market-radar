"""LLM client identity tests: the second-model anchor must really be a
different model/endpoint/key, and the request must use those values."""
import json

import httpx

from data.config import Config
from agents.llm import OpenAICompatLLM, make_second_llm


def test_complete_uses_model_name_base_url_and_api_key():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"p_yes": 0.5}'}}]})

    cfg = Config(llm_base_url="https://primary.example/v1", llm_api_key="pk",
                 model="primary")
    llm = OpenAICompatLLM(cfg, model="secondary",
                          base_url="https://secondary.example/v1", api_key="sk")
    llm._client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    schema = {"type": "object", "properties": {"p_yes": {"type": "number"}},
              "required": ["p_yes"], "additionalProperties": False}
    assert llm.complete("sys", "usr", schema) == {"p_yes": 0.5}
    assert captured["url"] == "https://secondary.example/v1/chat/completions"
    assert captured["auth"] == "Bearer sk"
    assert captured["model"] == "secondary"
    llm.close()


def test_second_llm_disabled_in_mock():
    assert make_second_llm(Config(mock=True)) is None


def test_second_llm_disabled_when_identical_to_primary():
    cfg = Config(llm_api_key="k", model="deepseek-chat", second_model="deepseek-chat")
    assert make_second_llm(cfg) is None


def test_second_llm_uses_own_identity():
    cfg = Config(llm_api_key="pk", llm_base_url="https://primary/v1", model="m1",
                 second_model="m2", second_model_base_url="https://second/v1",
                 second_model_api_key="sk2")
    second = make_second_llm(cfg)
    assert second is not None
    assert second.model_name == "m2"
    assert second.base_url == "https://second/v1"
    assert second.api_key == "sk2"


def test_complete_drops_response_format_and_strips_markdown_on_retry():
    """Free/domestic models that reject ``response_format`` or fence the JSON
    must still work: retry without response_format and recover the object."""
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '```json\n{"p_yes": 0.42}\n```'}}]})

    cfg = Config(llm_base_url="https://x/v1", llm_api_key="k", model="m")
    llm = OpenAICompatLLM(cfg)
    llm._client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    schema = {"type": "object", "properties": {"p_yes": {"type": "number"}},
              "required": ["p_yes"], "additionalProperties": False}
    assert llm.complete("s", "u", schema) == {"p_yes": 0.42}
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    llm.close()
