"""The Bedrock adapter, tested with no AWS, no boto3 session and no money.

    python -m unittest discover apps/spindle/web/tests

Same rule as `test_embed.py` and `test_spend.py`: offline by construction. Every client
here is a hand-written fake with the two or three methods the module actually calls, not a
`mock.patch` of boto3 — a stub that must be *given* a response cannot accidentally reach
the network, and it documents the API surface this module depends on in one place.

The assertions concentrate on failures that do not announce themselves:

  * a vector of the right length containing a `NaN` (every distance becomes `NaN`, the
    shortlist stops ranking, nothing raises);
  * batch output read back in file order instead of by `recordId` (every vector attributed
    to the wrong party, and again nothing raises);
  * a throttle treated as a transient blip when it is a permanent, non-adjustable zero;
  * a cost that was incurred and not recorded.

and on the one thing this module exists to make impossible: any of the above quietly
producing a working-looking result instead of an exception.
"""

from __future__ import annotations

import io
import json
import math
import os
import unittest
from decimal import Decimal
from unittest import mock

from spindle import bedrock, embed, spend


def _env(**overrides: str):
    return mock.patch.dict(os.environ, overrides, clear=True)


def _vec(n: int = 1024, fill: float = 0.1) -> list[float]:
    return [fill] * n


def _titan(values=None, tokens: int = 3) -> dict:
    return {"embedding": values if values is not None else _vec(),
            "inputTextTokenCount": tokens}


class FakeError(Exception):
    """A botocore `ClientError` as far as this module is concerned.

    `bedrock._error_code` reads `exc.response["Error"]["Code"]` structurally rather than
    importing `ClientError`, so this needs no boto3 — which is the point of doing it that
    way, and this class is the test of that decision as much as of the code.
    """

    def __init__(self, code: str, message: str = "") -> None:
        message = message or f"simulated {code}"
        super().__init__(f"An error occurred ({code}): {message}")
        self.response = {"Error": {"Code": code, "Message": message}}


#: The exact text AWS returns for an account that is not entitled to batch inference,
#: captured 2026-08-13 from a real `CreateModelInvocationJob` against account
#: 821135790223 with a real role and a real bucket. Copied verbatim because the
#: translation matches on prose, and a paraphrase would test nothing.
NOT_ENTITLED = ("Your account is not authorized to perform this action. Please create a "
                "support case (https://console.aws.amazon.com/support/home) with details "
                "about your use case and we will get back to you.")


class FakeRuntime:
    """A `bedrock-runtime` client with exactly one method."""

    def __init__(self, *payloads, raises: Exception | None = None) -> None:
        self.payloads = list(payloads)
        self.raises = raises
        self.calls: list[dict] = []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        payload = self.payloads.pop(0) if self.payloads else _titan()
        return {"body": io.BytesIO(json.dumps(payload).encode())}


# --------------------------------------------------------------- the payload

class RequestBody(unittest.TestCase):

    def test_pins_dimensions_and_normalize_explicitly(self):
        # normalize defaults to true at Titan's end today. Sending it means an AWS
        # default change cannot silently produce half an index of non-unit vectors.
        body = bedrock.request_body("hello", dimensions=1024)
        self.assertEqual(body, {"inputText": "hello", "dimensions": 1024,
                                "normalize": True})

    def test_unsupported_width_is_refused_before_the_call(self):
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.request_body("hello", dimensions=768)
        self.assertIn("768", str(caught.exception))

    def test_empty_text_is_refused_rather_than_embedded(self):
        # Titan would reject it too, but the local message names the real bug: a party
        # with no profile text, whose meaningless vector would sit in the index forever.
        for blank in ("", "   ", "\n"):
            with self.subTest(blank=blank), self.assertRaises(bedrock.BedrockUnavailable):
                bedrock.request_body(blank, dimensions=1024)


class ParseOutput(unittest.TestCase):

    def test_happy_path_returns_values_and_the_true_token_count(self):
        got = bedrock.parse_output(_titan(tokens=7), dimensions=1024, where="w")
        self.assertEqual(len(got.values), 1024)
        self.assertEqual(got.input_tokens, 7)

    def test_wrong_width_raises(self):
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_output(_titan(_vec(512)), dimensions=1024, where="w")
        self.assertIn("512", str(caught.exception))

    def test_a_nan_is_caught_here_because_nothing_downstream_would(self):
        poisoned = _vec()
        poisoned[17] = float("nan")
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_output(_titan(poisoned), dimensions=1024, where="w")
        self.assertIn("17", str(caught.exception))

    def test_an_infinity_is_caught_too(self):
        poisoned = _vec()
        poisoned[0] = float("inf")
        with self.assertRaises(bedrock.BedrockUnavailable):
            bedrock.parse_output(_titan(poisoned), dimensions=1024, where="w")

    def test_a_non_numeric_dimension_is_caught(self):
        poisoned = _vec()
        poisoned[3] = "0.5"  # type: ignore[call-overload]
        with self.assertRaises(bedrock.BedrockUnavailable):
            bedrock.parse_output(_titan(poisoned), dimensions=1024, where="w")

    def test_missing_token_count_refuses_rather_than_costing_zero(self):
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_output({"embedding": _vec()}, dimensions=1024, where="w")
        self.assertIn("inputTextTokenCount", str(caught.exception))

    def test_missing_embedding_says_what_it_got_instead(self):
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_output({"message": "no"}, dimensions=1024, where="w")
        self.assertIn("message", str(caught.exception))


# ------------------------------------------------------------ error translation

class Translation(unittest.TestCase):

    def _raise(self, code: str) -> Exception:
        client = FakeRuntime(raises=FakeError(code))
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.embed_one(client, "hello", dimensions=1024)
        return caught.exception

    def test_throttling_is_its_own_type_and_names_the_zero_quota(self):
        # The single most important message in this module. Generic advice for a
        # ThrottlingException is "back off and retry", which against a non-adjustable
        # zero is an infinite loop that looks like patience.
        exc = self._raise("ThrottlingException")
        self.assertIsInstance(exc, bedrock.BedrockThrottled)
        text = str(exc)
        self.assertIn("L-26C560CE", text)
        self.assertIn("zero", text.lower())
        self.assertIn("Adjustable: false", text)
        self.assertIn("Do not retry", text)
        self.assertIn(bedrock.BATCH_KEY, text)

    def test_access_denied_points_at_region_and_iam_not_at_retrying(self):
        text = str(self._raise("AccessDeniedException"))
        self.assertIn("PLATFORM_REGION", text)
        self.assertIn("InvokeModel", text)

    def test_validation_error_names_the_model_and_region(self):
        text = str(self._raise("ValidationException"))
        self.assertIn(bedrock.MODEL_ID, text)

    def test_account_entitlement_is_told_apart_from_a_bad_request_body(self):
        # AWS returns ValidationException for BOTH "your body is malformed" and "your
        # account may not do this at all". Telling an operator to check their request
        # body when no body would ever have worked is the wrong answer, so the message
        # is matched. This is the account's actual state as of 2026-08-13.
        control = FakeControl()

        def refuse(**kwargs):
            raise FakeError("ValidationException", NOT_ENTITLED)

        control.create_model_invocation_job = refuse
        with self.assertRaises(bedrock.BedrockNotEntitled) as caught:
            bedrock.submit_batch(["party %d" % i for i in range(100)], dimensions=1024,
                                 config=BatchJobLifecycle.CONFIG, control=control,
                                 s3=FakeS3())
        text = str(caught.exception)
        self.assertIn("support case", text)
        self.assertIn("not evidence", text.lower(),
                      "must warn that the quota row is not capability")
        self.assertIn("nothing to retry", text)

    def test_entitlement_failure_is_still_a_bedrock_error_for_the_port(self):
        # BedrockNotEntitled has to stay catchable as BedrockUnavailable, or
        # embed.BedrockBatchEmbedder's translation misses it and it escapes the port.
        self.assertTrue(issubclass(bedrock.BedrockNotEntitled, bedrock.BedrockUnavailable))
        self.assertTrue(issubclass(bedrock.BedrockThrottled, bedrock.BedrockUnavailable))
        self.assertTrue(issubclass(bedrock.BatchNotConfigured, bedrock.BedrockUnavailable))

    def test_a_plain_validation_error_is_not_mistaken_for_an_entitlement_one(self):
        exc = self._raise("ValidationException")
        self.assertNotIsInstance(exc, bedrock.BedrockNotEntitled)

    def test_an_untyped_failure_still_becomes_one_loud_bedrock_error(self):
        # No `.response` attribute at all — a connection reset, a botocore version skew.
        # It must not escape as some other exception class from an unrelated library.
        client = FakeRuntime(raises=OSError("connection reset"))
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.embed_one(client, "hello", dimensions=1024)
        self.assertIn("connection reset", str(caught.exception))

    def test_nothing_is_ever_swallowed_into_a_default_vector(self):
        # The rule this codebase cares about most: no failure produces a usable-looking
        # result. There is no code path through embed_one that returns without a vector.
        client = FakeRuntime(raises=FakeError("ThrottlingException"))
        with self.assertRaises(bedrock.BedrockUnavailable):
            bedrock.embed_one(client, "hello", dimensions=1024)


class OnDemand(unittest.TestCase):

    def test_sends_the_verified_body_and_content_types(self):
        client = FakeRuntime()
        bedrock.embed_one(client, "hello", dimensions=1024)
        call = client.calls[0]
        self.assertEqual(call["modelId"], "amazon.titan-embed-text-v2:0")
        self.assertEqual(json.loads(call["body"]),
                         {"inputText": "hello", "dimensions": 1024, "normalize": True})
        self.assertEqual(call["accept"], "application/json")
        self.assertEqual(call["contentType"], "application/json")

    def test_a_body_that_is_not_readable_raises(self):
        class Broken:
            def invoke_model(self, **kwargs):
                return {"body": None}

        with self.assertRaises(bedrock.BedrockUnavailable):
            bedrock.embed_one(Broken(), "hello", dimensions=1024)

    def test_non_json_body_raises(self):
        class Garbage:
            def invoke_model(self, **kwargs):
                return {"body": io.BytesIO(b"<html>throttled</html>")}

        with self.assertRaises(bedrock.BedrockUnavailable):
            bedrock.embed_one(Garbage(), "hello", dimensions=1024)


# ----------------------------------------------------------- Cohere on Bedrock
#
# The Titan tests above assert the shape of a path that cannot run on this account. These
# assert the shape of the one that can, and the interesting difference is where the truth
# lives: Titan reports its token count in the JSON body, Cohere reports it in an HTTP
# header. Everything that follows from that — a header being a string, being absent, or
# being nonsense — is a way for a paid call to look free, so it gets its own tests.


def _cohere(n: int = 1, dims: int = 1024, fill: float = 0.1) -> dict:
    return {"embeddings": [[fill] * dims for _ in range(n)],
            "id": "fake", "response_type": "embeddings_floats",
            "texts": ["t"] * n}


class FakeCohereRuntime:
    """A `bedrock-runtime` whose response carries headers, as the real one does."""

    def __init__(self, *payloads, tokens="4", raises: Exception | None = None,
                 headers: dict | None = None) -> None:
        self.payloads = list(payloads)
        self.tokens = tokens
        self.raises = raises
        self.headers = headers
        self.calls: list[dict] = []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        payload = self.payloads.pop(0) if self.payloads else _cohere()
        headers = self.headers
        if headers is None:
            headers = {bedrock.COHERE_TOKEN_HEADER: self.tokens}
        return {"body": io.BytesIO(json.dumps(payload).encode()),
                "ResponseMetadata": {"HTTPHeaders": headers}}


class CohereRequestBody(unittest.TestCase):

    def test_states_input_type_and_truncate_rather_than_defaulting_them(self):
        body = bedrock.cohere_request_body(["a", "b"], input_type="search_document")
        self.assertEqual(body, {"texts": ["a", "b"], "input_type": "search_document",
                                "truncate": "END"})

    def test_an_unknown_input_type_is_refused_before_the_call(self):
        # The real hazard: a wrong input_type does not error at Cohere, it just retrieves
        # worse. So it has to be refused locally or it is never noticed at all.
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.cohere_request_body(["a"], input_type="document")
        self.assertIn("search_document", str(caught.exception))

    def test_over_the_batch_ceiling_is_refused_naming_both_numbers(self):
        # 97 is a ValidationException that fails the whole call, measured 2026-08-16.
        texts = ["t"] * (bedrock.COHERE_MAX_BATCH + 1)
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.cohere_request_body(texts, input_type="search_document")
        self.assertIn(str(bedrock.COHERE_MAX_BATCH), str(caught.exception))
        self.assertIn("97", str(caught.exception))

    def test_an_empty_text_anywhere_in_the_batch_is_refused_by_position(self):
        # 95 good vectors and one meaningless one is the failure that never raises.
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.cohere_request_body(["fine", "   ", "also fine"],
                                        input_type="search_document")
        self.assertIn("position 1", str(caught.exception))

    def test_no_texts_at_all_is_refused(self):
        with self.assertRaises(bedrock.BedrockUnavailable):
            bedrock.cohere_request_body([], input_type="search_document")


class ParseCohereOutput(unittest.TestCase):

    def test_happy_path_returns_every_vector_and_the_billed_count(self):
        got = bedrock.parse_cohere_output(
            _cohere(n=3), input_tokens="17", count=3, dimensions=1024, where="w")
        self.assertEqual(len(got.values), 3)
        self.assertEqual({len(v) for v in got.values}, {1024})
        self.assertEqual(got.input_tokens, 17)

    def test_a_short_batch_raises_because_it_would_misalign_every_later_row(self):
        # Every returned vector is well-formed, so nothing downstream would notice that
        # row 40 is now carrying row 41's counterparty.
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_cohere_output(_cohere(n=2), input_tokens="4", count=3,
                                        dimensions=1024, where="w")
        self.assertIn("misalign", str(caught.exception))

    def test_a_nan_is_caught_here_because_nothing_downstream_would(self):
        payload = _cohere(n=1)
        payload["embeddings"][0][5] = float("nan")
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_cohere_output(payload, input_tokens="4", count=1,
                                        dimensions=1024, where="w")
        self.assertIn("stops ranking", str(caught.exception))

    def test_the_wrong_width_names_the_model_rather_than_the_request(self):
        # Cohere takes no dimensions argument, so a width change is a model change.
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_cohere_output(_cohere(n=1, dims=768), input_tokens="4",
                                        count=1, dimensions=1024, where="w")
        self.assertIn("768", str(caught.exception))

    def test_a_missing_token_header_refuses_rather_than_costing_zero(self):
        # The whole reason this is tested separately from Titan: the count is not in the
        # body, so "absent" is a thing that can happen without the response looking wrong.
        for absent in (None, "", "not-a-number"):
            with self.subTest(absent=absent), \
                    self.assertRaises(bedrock.BedrockUnavailable) as caught:
                bedrock.parse_cohere_output(_cohere(), input_tokens=absent, count=1,
                                            dimensions=1024, where="w")
            self.assertIn("uncosted paid call", str(caught.exception))

    def test_a_dict_of_embeddings_is_refused_rather_than_guessed_at(self):
        payload = _cohere()
        payload["embeddings"] = {"float": [[0.1] * 1024]}
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_cohere_output(payload, input_tokens="4", count=1,
                                        dimensions=1024, where="w")
        self.assertIn("embedding_types", str(caught.exception))


class CohereOnDemand(unittest.TestCase):

    def test_sends_the_model_id_and_content_types(self):
        client = FakeCohereRuntime()
        bedrock.embed_many_cohere(client, ["hello"], dimensions=1024,
                                  input_type="search_document")
        sent = client.calls[0]
        self.assertEqual(sent["modelId"], bedrock.COHERE_MODEL_ID)
        self.assertEqual(sent["accept"], "application/json")
        self.assertEqual(json.loads(sent["body"])["input_type"], "search_document")

    def test_a_width_this_model_cannot_produce_is_refused_before_the_call(self):
        client = FakeCohereRuntime()
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.embed_many_cohere(client, ["hello"], dimensions=768,
                                      input_type="search_document")
        self.assertIn("768", str(caught.exception))
        self.assertEqual(client.calls, [])

    def test_a_throttle_is_still_translated_to_the_bedrock_type(self):
        client = FakeCohereRuntime(raises=FakeError("ThrottlingException"))
        with self.assertRaises(bedrock.BedrockThrottled):
            bedrock.embed_many_cohere(client, ["hello"], dimensions=1024,
                                      input_type="search_document")

    def test_missing_response_metadata_is_a_refusal_not_a_free_call(self):
        client = FakeCohereRuntime(headers={})
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.embed_many_cohere(client, ["hello"], dimensions=1024,
                                      input_type="search_document")
        self.assertIn("uncosted paid call", str(caught.exception))

    def test_header_case_does_not_decide_whether_a_call_is_costed(self):
        # botocore lowercases these, but a dict lookup does not, so this must not depend
        # on that remaining true.
        client = FakeCohereRuntime(headers={"X-Amzn-Bedrock-Input-Token-Count": "9"})
        got = bedrock.embed_many_cohere(client, ["hello"], dimensions=1024,
                                        input_type="search_document")
        self.assertEqual(got.input_tokens, 9)


# ------------------------------------------------------------------ batch input

class BatchInput(unittest.TestCase):

    def test_record_ids_are_eleven_alphanumeric_characters(self):
        # Bedrock's JSONL schema requires exactly this. A 10- or 12-character id is a
        # whole-job rejection after the upload.
        for index in (0, 1, 99_999):
            rid = bedrock.record_id(index)
            self.assertEqual(len(rid), 11, rid)
            self.assertTrue(rid.isalnum(), rid)

    def test_record_ids_are_unique_and_order_encoding(self):
        ids = [bedrock.record_id(i) for i in range(1000)]
        self.assertEqual(len(set(ids)), 1000)
        self.assertEqual(ids, sorted(ids), "ids must sort into input order")

    def test_below_the_hundred_record_floor_is_refused_with_the_alternative(self):
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.input_jsonl(["t"] * 99, dimensions=1024)
        text = str(caught.exception)
        self.assertIn("100", text)
        self.assertIn("OpenAI", text)

    def test_above_the_hundred_thousand_ceiling_is_refused(self):
        # Built without materialising 100,001 strings.
        with mock.patch.object(bedrock, "BATCH_MAX_RECORDS", 200), \
                self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.input_jsonl(["t"] * 201, dimensions=1024)
        self.assertIn("201", str(caught.exception))

    def test_each_line_is_a_record_with_the_pinned_model_input(self):
        body = bedrock.input_jsonl(["t%d" % i for i in range(100)], dimensions=1024)
        lines = body.decode().strip().split("\n")
        self.assertEqual(len(lines), 100)
        first = json.loads(lines[0])
        self.assertEqual(first["recordId"], bedrock.record_id(0))
        self.assertEqual(first["modelInput"],
                         {"inputText": "t0", "dimensions": 1024, "normalize": True})

    def test_oversize_file_is_refused_before_upload(self):
        with mock.patch.object(bedrock, "BATCH_MAX_INPUT_BYTES", 100), \
                self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.input_jsonl(["a" * 50] * 100, dimensions=1024)
        self.assertIn("quota", str(caught.exception))


class BatchOutput(unittest.TestCase):

    def _line(self, index: int, fill: float) -> str:
        return json.dumps({"recordId": bedrock.record_id(index),
                           "modelInput": {"inputText": "x"},
                           "modelOutput": _titan(_vec(fill=fill), tokens=index + 1)})

    def test_output_is_reordered_by_record_id_not_by_file_position(self):
        # THE test in this file. Bedrock does not promise output order, and reading it
        # top-to-bottom attributes every vector to the wrong party — silently.
        body = "\n".join([self._line(2, 0.3), self._line(0, 0.1), self._line(1, 0.2)])
        got = bedrock.parse_batch_output(body, 3, dimensions=1024)
        self.assertEqual([e.values[0] for e in got], [0.1, 0.2, 0.3])

    def test_token_counts_survive_the_reordering(self):
        body = "\n".join([self._line(1, 0.2), self._line(0, 0.1)])
        got = bedrock.parse_batch_output(body, 2, dimensions=1024)
        self.assertEqual([e.input_tokens for e in got], [1, 2])
        self.assertEqual(bedrock.total_tokens(got), 3)

    def test_a_missing_record_fails_the_whole_read(self):
        # A job can report Completed with a short output file. Embedding 2 of 3 parties
        # and calling it success means nothing ever looks at the third again.
        body = "\n".join([self._line(0, 0.1), self._line(2, 0.3)])
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_batch_output(body, 3, dimensions=1024)
        self.assertIn(bedrock.record_id(1), str(caught.exception))

    def test_a_per_record_error_fails_the_whole_read(self):
        body = "\n".join([
            self._line(0, 0.1),
            json.dumps({"recordId": bedrock.record_id(1), "error": "InternalFailure"}),
        ])
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_batch_output(body, 2, dimensions=1024)
        self.assertIn("InternalFailure", str(caught.exception))

    def test_a_record_without_an_id_is_refused_rather_than_placed_by_position(self):
        body = json.dumps({"modelOutput": _titan()})
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.parse_batch_output(body, 1, dimensions=1024)
        self.assertIn("recordId", str(caught.exception))

    def test_a_poisoned_vector_inside_a_batch_is_caught_the_same_way(self):
        poisoned = _vec()
        poisoned[5] = float("nan")
        body = json.dumps({"recordId": bedrock.record_id(0),
                           "modelOutput": _titan(poisoned)})
        with self.assertRaises(bedrock.BedrockUnavailable):
            bedrock.parse_batch_output(body, 1, dimensions=1024)

    def test_blank_lines_are_tolerated(self):
        body = self._line(0, 0.1) + "\n\n"
        self.assertEqual(len(bedrock.parse_batch_output(body, 1, dimensions=1024)), 1)


# ---------------------------------------------------------------- batch config

class BatchConfiguration(unittest.TestCase):

    def test_missing_bucket_and_role_names_both_and_says_it_is_terraform(self):
        with _env(), self.assertRaises(bedrock.BatchNotConfigured) as caught:
            bedrock.load_batch_config()
        text = str(caught.exception)
        self.assertIn("RTF_BEDROCK_BATCH_BUCKET", text)
        self.assertIn("RTF_BEDROCK_BATCH_ROLE_ARN", text)
        self.assertIn("bedrock.amazonaws.com", text)

    def test_a_missing_role_alone_is_still_refused(self):
        # Half-configured must not be treated as configured. Bedrock reads S3 as the
        # role, so a bucket without one is a permissions failure eight minutes later.
        with _env(RTF_BEDROCK_BATCH_BUCKET="b"), \
                self.assertRaises(bedrock.BatchNotConfigured) as caught:
            bedrock.load_batch_config()
        self.assertIn("RTF_BEDROCK_BATCH_ROLE_ARN", str(caught.exception))
        self.assertNotIn("RTF_BEDROCK_BATCH_BUCKET", str(caught.exception))

    def test_prefix_has_a_default_because_it_is_a_name_not_a_capability(self):
        with _env(RTF_BEDROCK_BATCH_BUCKET="b", RTF_BEDROCK_BATCH_ROLE_ARN="arn:x"):
            config = bedrock.load_batch_config()
        self.assertEqual(config.bucket, "b")
        self.assertEqual(config.prefix, "bedrock-embed")
        self.assertEqual(config.uri("k/1.jsonl"), "s3://b/k/1.jsonl")


class FakeS3:
    def __init__(self, output: str = "") -> None:
        self.output = output
        self.puts: list[dict] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {}

    def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        return {"Contents": [{"Key": prefix + "manifest.json.out"},
                             {"Key": prefix + "input.jsonl.out"}]}

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(self.output.encode())}


class FakeControl:
    def __init__(self, *statuses: str) -> None:
        self.statuses = list(statuses) or ["Completed"]
        self.created: list[dict] = []

    def create_model_invocation_job(self, **kwargs):
        self.created.append(kwargs)
        return {"jobArn": "arn:aws:bedrock:us-east-1:821135790223:model-invocation-job/abc123"}

    def get_model_invocation_job(self, **kwargs):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"status": status, "message": f"simulated {status}"}


class BatchJobLifecycle(unittest.TestCase):

    CONFIG = bedrock.BatchConfig(bucket="rtf-batch", prefix="embed", role_arn="arn:role")

    def _texts(self, n: int = 100) -> list[str]:
        return [f"party {i}" for i in range(n)]

    def _output(self, n: int) -> str:
        return "\n".join(json.dumps({
            "recordId": bedrock.record_id(i),
            "modelOutput": _titan(_vec(fill=0.5), tokens=2),
        }) for i in range(n))

    def test_submit_uploads_jsonl_and_creates_the_job_with_the_role(self):
        s3, control = FakeS3(), FakeControl()
        job = bedrock.submit_batch(self._texts(), dimensions=1024, config=self.CONFIG,
                                   control=control, s3=s3, job_name="job1")
        self.assertEqual(s3.puts[0]["Bucket"], "rtf-batch")
        self.assertEqual(s3.puts[0]["Key"], "embed/job1/input.jsonl")
        created = control.created[0]
        self.assertEqual(created["modelId"], bedrock.MODEL_ID)
        self.assertEqual(created["roleArn"], "arn:role")
        self.assertEqual(
            created["inputDataConfig"]["s3InputDataConfig"]["s3Uri"],
            "s3://rtf-batch/embed/job1/input.jsonl")
        self.assertEqual(
            created["inputDataConfig"]["s3InputDataConfig"]["s3InputFormat"], "JSONL")
        self.assertEqual(job.job_id, "abc123")

    def test_a_job_arn_that_never_came_back_is_an_error_not_a_none(self):
        class NoArn(FakeControl):
            def create_model_invocation_job(self, **kwargs):
                return {}

        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.submit_batch(self._texts(), dimensions=1024, config=self.CONFIG,
                                 control=NoArn(), s3=FakeS3())
        self.assertIn("unreachable", str(caught.exception))

    def test_await_polls_until_terminal(self):
        control = FakeControl("Submitted", "InProgress", "Completed")
        slept: list[int] = []
        job = bedrock.BatchJob(arn="arn:x/abc", input_key="i", output_prefix="o")
        status = bedrock.await_batch(job, control=control, poll_seconds=1,
                                     sleep=slept.append)
        self.assertEqual(status, "Completed")
        self.assertEqual(slept, [1, 1])

    def test_partially_completed_is_a_failure_not_a_smaller_success(self):
        job = bedrock.BatchJob(arn="arn:x/abc", input_key="i", output_prefix="o")
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.await_batch(job, control=FakeControl("PartiallyCompleted"),
                                sleep=lambda _: None)
        self.assertIn("PartiallyCompleted", str(caught.exception))

    def test_failed_job_surfaces_the_reason(self):
        job = bedrock.BatchJob(arn="arn:x/abc", input_key="i", output_prefix="o")
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.await_batch(job, control=FakeControl("Failed"), sleep=lambda _: None)
        self.assertIn("simulated Failed", str(caught.exception))

    def test_timeout_keeps_the_arn_in_the_message(self):
        # The job is still running and still billing after we stop watching. A caller
        # that got None back would have no handle on it.
        job = bedrock.BatchJob(arn="arn:x/abc", input_key="i", output_prefix="o")
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.await_batch(job, control=FakeControl("InProgress"),
                                timeout_seconds=0, sleep=lambda _: None)
        self.assertIn("arn:x/abc", str(caught.exception))

    def test_fetch_skips_the_manifest_and_reads_the_output(self):
        job = bedrock.BatchJob(arn="arn:x/abc123", input_key="i",
                               output_prefix="embed/job1/out")
        got = bedrock.fetch_batch_output(job, 100, dimensions=1024, config=self.CONFIG,
                                         s3=FakeS3(self._output(100)))
        self.assertEqual(len(got), 100)

    def test_ambiguous_output_listing_refuses_to_guess(self):
        class TwoOutputs(FakeS3):
            def list_objects_v2(self, **kwargs):
                p = kwargs["Prefix"]
                return {"Contents": [{"Key": p + "a.out"}, {"Key": p + "b.out"}]}

        job = bedrock.BatchJob(arn="arn:x/abc123", input_key="i", output_prefix="o")
        with self.assertRaises(bedrock.BedrockUnavailable) as caught:
            bedrock.fetch_batch_output(job, 1, dimensions=1024, config=self.CONFIG,
                                       s3=TwoOutputs())
        self.assertIn("exactly one", str(caught.exception))

    def test_end_to_end_returns_vectors_in_input_order(self):
        got = bedrock.embed_batch_job(
            self._texts(), dimensions=1024, config=self.CONFIG,
            control=FakeControl(), s3=FakeS3(self._output(100)), sleep=lambda _: None)
        self.assertEqual(len(got), 100)
        self.assertTrue(all(len(e.values) == 1024 for e in got))


# ----------------------------------------------------- the port and the gate

class Port(unittest.TestCase):

    def test_on_demand_adapter_produces_schema_width_vectors_with_the_right_model(self):
        embedder = embed.BedrockEmbedder(client=FakeRuntime(_titan(), _titan()))
        vectors = embedder.embed(["a", "b"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0].values), embed.DIMENSIONS)
        self.assertEqual(vectors[0].model, "bedrock:amazon.titan-embed-text-v2:0")

    def test_batch_and_on_demand_write_the_same_embedding_model(self):
        # If these ever diverge the party_shortlist index splits in two over a price,
        # and every shortlist silently loses half its candidates.
        self.assertEqual(embed.BedrockEmbedder.model, embed.BedrockBatchEmbedder.model)
        self.assertNotEqual(embed.BedrockEmbedder.key, embed.BedrockBatchEmbedder.key)

    def test_a_bedrock_failure_becomes_the_ports_exception_and_keeps_the_cause(self):
        embedder = embed.BedrockEmbedder(
            client=FakeRuntime(raises=FakeError("ThrottlingException")))
        with self.assertRaises(embed.EmbeddingUnavailable) as caught:
            embedder.embed(["a"])
        self.assertIsInstance(caught.exception.__cause__, bedrock.BedrockThrottled)
        self.assertIn("L-26C560CE", str(caught.exception))

    def test_a_failure_never_yields_a_short_list_of_vectors(self):
        # The failure mode that would matter most: three texts in, two vectors out, and
        # a caller that zips them against three party ids.
        client = FakeRuntime(_titan())
        client.payloads = [_titan()]

        class FailsSecond:
            def __init__(self):
                self.n = 0

            def invoke_model(self, **kwargs):
                self.n += 1
                if self.n == 2:
                    raise FakeError("ThrottlingException")
                return {"body": io.BytesIO(json.dumps(_titan()).encode())}

        with self.assertRaises(embed.EmbeddingUnavailable):
            embed.BedrockEmbedder(client=FailsSecond()).embed(["a", "b", "c"])

    def test_the_batch_entitlement_wall_reaches_the_caller_intact(self):
        # The end-to-end shape of today's actual failure: a backfill selects its rows,
        # calls the port, and gets one exception that names a support case. It must not
        # arrive as a botocore ValidationException about a request body.
        control = FakeControl()

        def refuse(**kwargs):
            raise FakeError("ValidationException", NOT_ENTITLED)

        control.create_model_invocation_job = refuse
        embedder = embed.BedrockBatchEmbedder(
            config=BatchJobLifecycle.CONFIG, control=control, s3=FakeS3())
        with self.assertRaises(embed.EmbeddingUnavailable) as caught:
            embedder.embed(["party %d" % i for i in range(100)])
        self.assertIsInstance(caught.exception.__cause__, bedrock.BedrockNotEntitled)
        self.assertIn("support case", str(caught.exception))

    def test_every_bedrock_key_is_priced(self):
        for key in (bedrock.ON_DEMAND_KEY, bedrock.BATCH_KEY,
                    embed.BedrockEmbedder.key, embed.BedrockBatchEmbedder.key):
            self.assertIn(key, spend.RATES, f"{key} has no rate card entry")

    def test_batch_is_priced_below_on_demand(self):
        on_demand = spend.RATES[bedrock.ON_DEMAND_KEY].usd_per_mtok_in
        batch = spend.RATES[bedrock.BATCH_KEY].usd_per_mtok_in
        self.assertEqual(on_demand, Decimal("0.02"))
        self.assertEqual(batch, Decimal("0.01"))
        self.assertLess(batch, on_demand)

    def test_cost_is_computed_through_the_rate_card(self):
        # One million tokens of batch embedding is ten cents' worth at $0.01/Mtok.
        self.assertEqual(spend.estimate(bedrock.BATCH_KEY, tokens_in=1_000_000),
                         Decimal("0.010000"))
        self.assertEqual(spend.estimate(bedrock.ON_DEMAND_KEY, tokens_in=1_000_000),
                         Decimal("0.020000"))

    def test_selecting_bedrock_batch_without_configuration_raises(self):
        with _env(RTF_EMBED_PROVIDER="bedrock-batch"), \
                self.assertRaises(embed.EmbeddingUnavailable) as caught:
            embed.load()
        self.assertIn("RTF_BEDROCK_BATCH_BUCKET", str(caught.exception))

    def test_bedrock_is_never_inferred_from_an_empty_environment(self):
        with _env(), self.assertRaises(embed.EmbeddingUnavailable) as caught:
            embed.load()
        self.assertIn("No embedding provider configured", str(caught.exception))

    def test_the_no_provider_message_names_the_batch_option(self):
        with _env(), self.assertRaises(embed.EmbeddingUnavailable) as caught:
            embed.load()
        self.assertIn("bedrock-batch", str(caught.exception))


class GatedSpend(unittest.TestCase):
    """Cost has to flow through `spend.Gate`, including when the call fails halfway."""

    LONG = "a" * 4000

    def _gate(self, **env: str) -> spend.Gate:
        with _env(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD="1.00", **env):
            return spend.Gate.open(None, None)

    def test_closed_gate_stops_the_batch_path_before_a_job_is_created(self):
        control, s3 = FakeControl(), FakeS3()
        embedder = embed.BedrockBatchEmbedder(
            config=BatchJobLifecycle.CONFIG, control=control, s3=s3)
        with _env():
            gate = spend.Gate.open(None, None)
        with self.assertRaises(spend.SpendRefused):
            embed.embed_bulk(gate, embedder, [self.LONG] * 100)
        self.assertEqual(control.created, [], "a job was created despite a closed gate")
        self.assertEqual(s3.puts, [], "input was uploaded despite a closed gate")

    def test_bulk_costs_at_the_batch_rate_and_records_it(self):
        texts = [self.LONG] * 100
        output = "\n".join(json.dumps({
            "recordId": bedrock.record_id(i),
            "modelOutput": _titan(_vec(fill=0.5), tokens=1000),
        }) for i in range(100))
        embedder = embed.BedrockBatchEmbedder(
            config=BatchJobLifecycle.CONFIG, control=FakeControl(),
            s3=FakeS3(output), timeout_seconds=1)
        gate = self._gate()
        vectors, spent = embed.embed_bulk(gate, embedder, texts)
        self.assertEqual(len(vectors), 100)
        expected = spend.estimate(bedrock.BATCH_KEY,
                                  tokens_in=embed.estimate_tokens(texts))
        self.assertEqual(spent, expected)
        self.assertEqual(gate.incurred_usd, expected)

    def test_bulk_chunks_at_the_job_quota_and_gates_each_job(self):
        # 250 texts at a job size of 100 is three jobs and three gate checks — the
        # granularity at which a ceiling can actually stop a Bedrock backfill.
        created: list[int] = []

        class Counting:
            key = bedrock.BATCH_KEY
            model = bedrock.MODEL

            def embed(self, texts):
                created.append(len(texts))
                return [embed.Vector(_vec(), self.model) for _ in texts]

        vectors, _ = embed.embed_bulk(self._gate(), Counting(), ["t"] * 250, job_size=100)
        self.assertEqual(created, [100, 100, 50])
        self.assertEqual(len(vectors), 250)

    def test_bulk_refuses_a_job_size_over_the_quota(self):
        class Never:
            key = bedrock.BATCH_KEY
            model = bedrock.MODEL

            def embed(self, texts):
                raise AssertionError("must not be called")

        with self.assertRaises(embed.EmbeddingUnavailable):
            embed.embed_bulk(self._gate(), Never(), ["t"] * 10,
                             job_size=bedrock.BATCH_MAX_RECORDS + 1)

    def test_a_partly_billed_on_demand_batch_records_what_it_spent(self):
        # Titan bills per invocation, so failing on the ninth of sixteen means eight
        # were paid for. Recording zero there hands the same money out twice.
        class FailsAfterEight:
            key = bedrock.ON_DEMAND_KEY
            model = bedrock.MODEL

            def embed(self, texts):
                raise embed.EmbeddingUnavailable("throttled on the ninth", completed=8)

        gate = self._gate()
        with self.assertRaises(embed.EmbeddingUnavailable):
            embed.embed_batch(gate, FailsAfterEight(), [self.LONG] * 16)
        full = spend.estimate(bedrock.ON_DEMAND_KEY,
                              tokens_in=embed.estimate_tokens([self.LONG] * 16))
        self.assertGreater(gate.incurred_usd, Decimal("0"),
                           "a partly-billed batch recorded nothing")
        self.assertLess(gate.incurred_usd, full)

    def test_a_batch_that_failed_before_billing_records_nothing(self):
        class FailsImmediately:
            key = bedrock.ON_DEMAND_KEY
            model = bedrock.MODEL

            def embed(self, texts):
                raise embed.EmbeddingUnavailable("throttled on the first")

        gate = self._gate()
        with self.assertRaises(embed.EmbeddingUnavailable):
            embed.embed_batch(gate, FailsImmediately(), [self.LONG] * 16)
        self.assertEqual(gate.incurred_usd, Decimal("0"))


class Constants(unittest.TestCase):
    """The measured quota numbers, asserted so a silent edit shows up as a failure."""

    def test_quota_ceilings_match_what_was_measured(self):
        self.assertEqual(bedrock.BATCH_MAX_RECORDS, 100_000)
        self.assertEqual(bedrock.BATCH_MIN_RECORDS, 100)
        self.assertEqual(bedrock.BATCH_MAX_INPUT_BYTES, 1_000_000_000)

    def test_the_model_key_is_the_one_the_index_prefix_already_holds(self):
        self.assertEqual(bedrock.MODEL, "bedrock:amazon.titan-embed-text-v2:0")
        self.assertEqual(bedrock.BATCH_KEY,
                         "bedrock:amazon.titan-embed-text-v2:0:batch")

    def test_the_schema_width_is_a_supported_titan_width(self):
        self.assertIn(embed.DIMENSIONS, bedrock.SUPPORTED_DIMENSIONS)

    def test_normalised_vectors_are_what_we_asked_for(self):
        # Not a test of Titan — a test that the flag we send is the one that makes a
        # unit vector, documented so the next person does not have to rediscover it.
        self.assertTrue(bedrock.request_body("x", dimensions=1024)["normalize"])
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in _vec(1, 1.0))), 1.0)


if __name__ == "__main__":
    unittest.main()
