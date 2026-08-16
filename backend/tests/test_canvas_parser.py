"""canvas_parser 单元测试：_extract_json、解析失败返回 None、增量只传新消息。"""
import json
import unittest
from unittest.mock import AsyncMock, patch

from core import canvas_parser


VALID_TREE = {"root": "自动写周报", "branches": [{"name": "核心功能", "children": []}]}
VALID_TREE_JSON = json.dumps(VALID_TREE, ensure_ascii=False)


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(canvas_parser._extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_json_block(self):
        text = "结果如下：\n```json\n{\"a\": 1}\n```\n完毕"
        self.assertEqual(canvas_parser._extract_json(text), {"a": 1})

    def test_generic_fence(self):
        text = "```\n{\"a\": 2}\n```"
        self.assertEqual(canvas_parser._extract_json(text), {"a": 2})

    def test_invalid_json_returns_none(self):
        self.assertIsNone(canvas_parser._extract_json("这不是 JSON"))
        self.assertIsNone(canvas_parser._extract_json("```json\n{broken\n```"))

    def test_non_dict_json_returns_none(self):
        self.assertIsNone(canvas_parser._extract_json("[1, 2, 3]"))


class ParseTreeTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_failure_returns_none(self):
        with patch.object(canvas_parser, "llm_complete", new=AsyncMock(return_value="模型胡言乱语")):
            tree = await canvas_parser.parse_conversation_to_tree([{"role": "user", "content": "hi"}])
        self.assertIsNone(tree)

    async def test_parse_success_returns_tree(self):
        fenced = f"```json\n{VALID_TREE_JSON}\n```"
        with patch.object(canvas_parser, "llm_complete", new=AsyncMock(return_value=fenced)):
            tree = await canvas_parser.parse_conversation_to_tree([{"role": "user", "content": "hi"}])
        self.assertEqual(tree, VALID_TREE)

    async def test_incremental_only_passes_new_messages(self):
        messages = [
            {"role": "user", "content": "旧消息一"},
            {"role": "assistant", "content": "旧回答一", "role_name": "cto"},
            {"role": "user", "content": "旧消息二"},
            {"role": "assistant", "content": "旧回答二", "role_name": "ops"},
            {"role": "user", "content": "新消息一"},
            {"role": "assistant", "content": "新回答一", "role_name": "designer"},
        ]
        mock = AsyncMock(return_value=f"```json\n{VALID_TREE_JSON}\n```")
        with patch.object(canvas_parser, "llm_complete", new=mock):
            tree = await canvas_parser.parse_incremental(messages, existing_tree=VALID_TREE, last_idx=4)
        self.assertEqual(tree, VALID_TREE)
        prompt = mock.await_args.args[0][1]["content"]
        self.assertIn("新消息一", prompt)
        self.assertNotIn("旧消息一", prompt)
        self.assertNotIn("旧消息二", prompt)

    async def test_incremental_without_new_messages_returns_existing(self):
        messages = [{"role": "user", "content": "旧消息"}]
        mock = AsyncMock()
        with patch.object(canvas_parser, "llm_complete", new=mock):
            tree = await canvas_parser.parse_incremental(messages, existing_tree=VALID_TREE, last_idx=5)
        self.assertIs(tree, VALID_TREE)
        mock.assert_not_awaited()

    async def test_incremental_without_existing_falls_back_to_full_parse(self):
        messages = [{"role": "user", "content": "全量消息"}]
        mock = AsyncMock(return_value=f"```json\n{VALID_TREE_JSON}\n```")
        with patch.object(canvas_parser, "llm_complete", new=mock):
            tree = await canvas_parser.parse_incremental(messages, existing_tree=None, last_idx=1)
        self.assertEqual(tree, VALID_TREE)
        self.assertIn("全量消息", mock.await_args.args[0][1]["content"])


if __name__ == "__main__":
    unittest.main()
