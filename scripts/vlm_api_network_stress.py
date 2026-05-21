#!/usr/bin/env python3
"""OpenAI-compatible VLM/API network and keep-alive stress tester.

This script intentionally avoids importing the local VLM Agent code. It uses
only environment configuration plus httpx to exercise the configured model
gateway with large request bodies and long-running responses.
"""

from __future__ import annotations

import argparse
import base64
import collections
import json
import os
import random
import socket
import ssl
import string
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

try:
    import httpx
except ImportError:  # pragma: no cover
    print("Missing dependency: httpx. Install with: python -m pip install httpx", file=sys.stderr)
    raise


ENV_API_KEY = ("VLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")
ENV_BASE_URL = ("VLM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE")
ENV_MODEL = ("VLM_MODEL", "OPENAI_MODEL", "MODEL")


@dataclass
class Result:
    phase: str
    index: int
    ok: bool
    status_code: int | None
    latency_s: float
    request_bytes: int
    response_bytes: int
    error_type: str | None = None
    errno: int | None = None


@dataclass
class Experiment:
    name: str
    use_proxy: bool
    rounds: int
    payload_mb: float
    long_rounds: int
    long_max_tokens: int
    long_prompt: str = ""


def first_env(names: tuple[str, ...], default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def chat_url(base_url: str) -> str:
    base = base_url.rstrip("/") + "/"
    if base.endswith("/chat/completions/"):
        return base[:-1]
    if base.endswith("/chat/completions"):
        return base
    return urljoin(base, "chat/completions")


def models_url(base_url: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", "models")


def random_text(size_bytes: int) -> str:
    rng = random.Random(20260520)
    alphabet = string.ascii_letters + string.digits + " .,;:-_/\\n"
    chunk = "".join(rng.choice(alphabet) for _ in range(8192))
    pieces = []
    remaining = size_bytes
    while remaining > 0:
        part = chunk[: min(len(chunk), remaining)]
        pieces.append(part)
        remaining -= len(part.encode("utf-8"))
    return "".join(pieces)[:size_bytes]


def image_data_url(path: str) -> str:
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    suffix = os.path.splitext(path)[1].lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{data}"


def payload_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def unwrap_errno(exc: BaseException) -> int | None:
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)
        errno = getattr(current, "errno", None) or getattr(current, "winerror", None)
        if isinstance(errno, int):
            return errno
        for attr in ("__cause__", "__context__"):
            child = getattr(current, attr, None)
            if isinstance(child, BaseException):
                stack.append(child)
        if getattr(current, "args", None):
            for arg in current.args:
                if isinstance(arg, BaseException):
                    stack.append(arg)
    return None


def post_chat(client: httpx.Client, url: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, bytes]:
    response = client.post(url, headers=headers, json=payload)
    body = response.content
    if response.status_code >= 400:
        print(f"HTTP {response.status_code}: {body[:1000].decode('utf-8', errors='replace')}")
    response.raise_for_status()
    return response.status_code, body


def make_large_payload(args: argparse.Namespace, large_text: str, image_url: str | None) -> dict[str, Any]:
    if image_url:
        content: Any = [
            {
                "type": "text",
                "text": "Network stress test. Briefly acknowledge the image and ignore any random padding.",
            },
            {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
            {"type": "text", "text": large_text},
        ]
    else:
        content = (
            "Network stress test. Ignore the random padding and reply with exactly "
            "'OK large payload received'.\n\n"
            f"<padding>\n{large_text}\n</padding>"
        )
    return {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "You are a connectivity test endpoint. Keep replies concise."},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": args.large_max_tokens,
    }


def make_long_payload(args: argparse.Namespace, prompt: str | None = None) -> dict[str, Any]:
    return {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "You are a network keep-alive stress test endpoint."},
            {
                "role": "user",
                "content": prompt or (
                    "Generate a long, structured diagnostic essay in Chinese. "
                    "Do not stop early. Include numbered sections and detailed examples. "
                    "This request is intended to keep the HTTP connection open."
                ),
            },
        ],
        "temperature": 0.7,
        "max_tokens": args.long_max_tokens,
    }


def run_request(
    *,
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    phase: str,
    index: int,
) -> Result:
    req_bytes = payload_size(payload)
    start = time.perf_counter()
    try:
        status_code, body = post_chat(client, url, headers, payload)
        latency = time.perf_counter() - start
        resp_bytes = len(body)
        throughput = (req_bytes + resp_bytes) / max(latency, 1e-9) / (1024 * 1024)
        print(
            f"[{phase} #{index}] OK status={status_code} "
            f"latency={latency:.2f}s request={req_bytes/1024/1024:.2f}MiB "
            f"response={resp_bytes/1024:.1f}KiB throughput={throughput:.2f}MiB/s"
        )
        return Result(phase, index, True, status_code, latency, req_bytes, resp_bytes)
    except Exception as exc:  # noqa: BLE001 - diagnostic script prints everything.
        latency = time.perf_counter() - start
        errno = unwrap_errno(exc)
        print(
            f"[{phase} #{index}] FAIL latency={latency:.2f}s "
            f"request={req_bytes/1024/1024:.2f}MiB error={type(exc).__name__} errno={errno}"
        )
        traceback.print_exc()
        return Result(
            phase=phase,
            index=index,
            ok=False,
            status_code=getattr(getattr(exc, "response", None), "status_code", None),
            latency_s=latency,
            request_bytes=req_bytes,
            response_bytes=0,
            error_type=type(exc).__name__,
            errno=errno,
        )


def print_report(results: list[Result]) -> None:
    print("\n========== Network Stability Report ==========")
    total = len(results)
    ok = sum(1 for r in results if r.ok)
    fail = total - ok
    success_rate = (ok / total * 100) if total else 0
    latencies = [r.latency_s for r in results if r.ok]
    print(f"total_requests={total}")
    print(f"success={ok}")
    print(f"failed={fail}")
    print(f"success_rate={success_rate:.1f}%")
    if latencies:
        print(f"avg_latency={sum(latencies)/len(latencies):.2f}s")
        print(f"min_latency={min(latencies):.2f}s")
        print(f"max_latency={max(latencies):.2f}s")
    errors = collections.Counter(
        f"{r.error_type or 'HTTP'}:{r.errno or r.status_code or 'unknown'}"
        for r in results
        if not r.ok
    )
    print("error_distribution=")
    if errors:
        for key, count in errors.most_common():
            print(f"  {key}: {count}")
    else:
        print("  none")
    by_phase: dict[str, list[Result]] = collections.defaultdict(list)
    for result in results:
        by_phase[result.phase].append(result)
    print("phase_summary=")
    for phase, rows in by_phase.items():
        phase_ok = sum(1 for r in rows if r.ok)
        print(f"  {phase}: {phase_ok}/{len(rows)} ok")
    print("==============================================")


def summarize_results(results: list[Result]) -> dict[str, Any]:
    total = len(results)
    ok = sum(1 for r in results if r.ok)
    latencies = [r.latency_s for r in results if r.ok]
    errors = collections.Counter(
        f"{r.error_type or 'HTTP'}:{r.errno or r.status_code or 'unknown'}"
        for r in results
        if not r.ok
    )
    return {
        "total": total,
        "ok": ok,
        "success_rate": (ok / total * 100) if total else 0.0,
        "avg_latency": (sum(latencies) / len(latencies)) if latencies else None,
        "error_distribution": errors,
    }


def print_matrix_report(experiment_results: list[tuple[Experiment, list[Result]]]) -> None:
    print("\n-----------------------------------------------")
    for idx, (experiment, results) in enumerate(experiment_results, start=1):
        summary = summarize_results(results)
        errors = summary["error_distribution"]
        error_text = "none" if not errors else ", ".join(f"{k} x{v}" for k, v in errors.most_common())
        proxy = "有代理" if experiment.use_proxy else "直连不带代理"
        long = f", long_max_tokens={experiment.long_max_tokens}" if experiment.long_rounds else ""
        print(
            f"实验{idx} ({experiment.name}; {proxy}; payload={experiment.payload_mb}MiB{long}): "
            f"{summary['ok']}/{summary['total']} 成功 "
            f"({summary['success_rate']:.1f}%) / {error_text}"
        )
    print("-----------------------------------------------")
    print("结论推导逻辑:")
    by_name = {experiment.name: summarize_results(results) for experiment, results in experiment_results}
    proxy_large = by_name.get("proxy_large_1MiB")
    direct_large = by_name.get("direct_large_1MiB")
    proxy_small_long = by_name.get("proxy_small_long")
    if proxy_large and direct_large:
        if proxy_large["success_rate"] < 100 and direct_large["success_rate"] == 100:
            print("- 实验二全成功，而实验一失败：元凶基本锁定为 127.0.0.1:7890 代理/TUN/Mixin/规则层。")
        elif direct_large["success_rate"] < 100:
            print("- 实验二也失败：DashScope 网关限制或本地 ISP/网络对大 HTTP POST 的处理仍然可疑。")
        else:
            print("- 实验一和实验二都成功：之前的问题可能是间歇性，建议增加 rounds 或 payload 继续压。")
    if proxy_large and proxy_small_long:
        if proxy_large["success_rate"] < 100 and proxy_small_long["success_rate"] == 100:
            print("- 有代理大包失败、小包长响应成功：更像代理对上传大包/body size 敏感。")
        elif proxy_small_long["success_rate"] < 100:
            print("- 有代理小包长响应也失败：代理长连接/keep-alive 稳定性同样可疑。")
    print("-----------------------------------------------")


def network_preflight(base_url: str, timeout_s: float = 5.0) -> None:
    host = base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    print("\nNetwork preflight:")
    print(f"  host={host}")
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        addrs = sorted({info[4][0] for info in infos})
        print(f"  dns={', '.join(addrs)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  dns_error={type(exc).__name__}: {exc}")
    try:
        start = time.perf_counter()
        raw = socket.create_connection((host, 443), timeout=timeout_s)
        with raw:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                print(
                    f"  tcp_tls=ok latency={time.perf_counter() - start:.2f}s "
                    f"tls={tls.version()} cipher={tls.cipher()[0]}"
                )
    except Exception as exc:  # noqa: BLE001
        print(f"  tcp_tls_error={type(exc).__name__}: {exc}")
    ping_cmd = ["ping", "-n", "4", host] if os.name == "nt" else ["ping", "-c", "4", host]
    try:
        proc = subprocess.run(ping_cmd, capture_output=True, text=True, timeout=15)
        text = (proc.stdout or proc.stderr).strip().replace("\r", "")
        print("  ping:")
        for line in text.splitlines()[-6:]:
            print(f"    {line}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ping_error={type(exc).__name__}: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress test OpenAI-compatible API connectivity.")
    parser.add_argument("--api-key", default=first_env(ENV_API_KEY), help="Defaults to VLM_API_KEY/OPENAI_API_KEY.")
    parser.add_argument("--base-url", default=first_env(ENV_BASE_URL), help="Defaults to VLM_BASE_URL/OPENAI_BASE_URL.")
    parser.add_argument("--model", default=first_env(ENV_MODEL), help="Defaults to VLM_MODEL/OPENAI_MODEL.")
    parser.add_argument("--rounds", type=int, default=10, help="Large-payload request count.")
    parser.add_argument("--payload-mb", type=float, default=5.0, help="Random text payload size per large request.")
    parser.add_argument("--image", default="", help="Optional local image to include as base64 data URL.")
    parser.add_argument("--large-max-tokens", type=int, default=32)
    parser.add_argument("--long-rounds", type=int, default=2)
    parser.add_argument("--long-max-tokens", type=int, default=2048)
    parser.add_argument("--connect-timeout", type=float, default=30)
    parser.add_argument("--read-timeout", type=float, default=360)
    parser.add_argument("--write-timeout", type=float, default=360)
    parser.add_argument("--pool-timeout", type=float, default=30)
    parser.add_argument("--no-models-preflight", action="store_true")
    parser.add_argument("--http2", action="store_true", help="Enable HTTP/2 if the gateway supports it.")
    parser.add_argument("--matrix", action="store_true", help="Run proxy/direct control-group experiments.")
    parser.add_argument("--proxy-url", default=os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "http://127.0.0.1:7890")
    parser.add_argument("--skip-network-preflight", action="store_true")
    return parser.parse_args()


def run_experiment(args: argparse.Namespace, experiment: Experiment) -> list[Result]:
    print(f"\n===== Running {experiment.name} =====")
    print(f"  proxy={'on ' + args.proxy_url if experiment.use_proxy else 'off/direct'}")
    print(f"  payload_mb={experiment.payload_mb} rounds={experiment.rounds}")
    print(f"  long_rounds={experiment.long_rounds} long_max_tokens={experiment.long_max_tokens}")

    saved_proxy_env = {
        key: os.environ.get(key)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    }
    if experiment.use_proxy:
        os.environ["HTTP_PROXY"] = args.proxy_url
        os.environ["HTTPS_PROXY"] = args.proxy_url
    else:
        for key in saved_proxy_env:
            os.environ.pop(key, None)

    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(
        connect=args.connect_timeout,
        read=args.read_timeout,
        write=args.write_timeout,
        pool=args.pool_timeout,
    )
    url = chat_url(args.base_url)
    large_text = random_text(int(experiment.payload_mb * 1024 * 1024))
    img_url = image_data_url(args.image) if args.image else None
    results: list[Result] = []

    try:
        with httpx.Client(timeout=timeout, trust_env=True, http2=args.http2) as client:
            if not args.no_models_preflight:
                try:
                    start = time.perf_counter()
                    response = client.get(models_url(args.base_url), headers=headers)
                    print(f"  /models preflight status={response.status_code} latency={time.perf_counter() - start:.2f}s")
                except Exception as exc:  # noqa: BLE001
                    print(f"  /models preflight failed: {type(exc).__name__}: {exc}")

            for i in range(1, experiment.rounds + 1):
                payload_args = argparse.Namespace(**vars(args))
                payload_args.large_max_tokens = args.large_max_tokens
                payload = make_large_payload(payload_args, large_text, img_url)
                results.append(run_request(
                    client=client,
                    url=url,
                    headers=headers,
                    payload=payload,
                    phase=experiment.name,
                    index=i,
                ))

            for i in range(1, experiment.long_rounds + 1):
                payload_args = argparse.Namespace(**vars(args))
                payload_args.long_max_tokens = experiment.long_max_tokens
                payload = make_long_payload(payload_args, experiment.long_prompt)
                results.append(run_request(
                    client=client,
                    url=url,
                    headers=headers,
                    payload=payload,
                    phase=f"{experiment.name}_long",
                    index=i,
                ))
    finally:
        for key, value in saved_proxy_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    return results


def run_matrix(args: argparse.Namespace) -> int:
    if not args.skip_network_preflight:
        # This preflight intentionally uses direct socket/TLS and ping. It does
        # not prove large POST stability, but quickly exposes DNS/TCP loss.
        network_preflight(args.base_url)

    long_prompt = (
        "请写一篇3000字左右关于深度学习历史的中文长文。"
        "要求从感知机、反向传播、卷积网络、ImageNet、Transformer、"
        "大语言模型、多模态模型一路讲到工程部署挑战。"
        "请尽量展开，不要过早结束。"
    )
    experiments = [
        Experiment(
            name="proxy_large_1MiB",
            use_proxy=True,
            rounds=3,
            payload_mb=1.0,
            long_rounds=0,
            long_max_tokens=args.long_max_tokens,
        ),
        Experiment(
            name="direct_large_1MiB",
            use_proxy=False,
            rounds=3,
            payload_mb=1.0,
            long_rounds=0,
            long_max_tokens=args.long_max_tokens,
        ),
        Experiment(
            name="proxy_small_long",
            use_proxy=True,
            rounds=1,
            payload_mb=0.1,
            long_rounds=1,
            long_max_tokens=max(args.long_max_tokens, 2048),
            long_prompt=long_prompt,
        ),
    ]

    experiment_results: list[tuple[Experiment, list[Result]]] = []
    for experiment in experiments:
        experiment_results.append((experiment, run_experiment(args, experiment)))
    print_matrix_report(experiment_results)
    return 0 if all(result.ok for _, rows in experiment_results for result in rows) else 1


def main() -> int:
    args = parse_args()
    missing = [
        name
        for name, value in {
            "api key": args.api_key,
            "base url": args.base_url,
            "model": args.model,
        }.items()
        if not value
    ]
    if missing:
        print(f"Missing required config: {', '.join(missing)}", file=sys.stderr)
        print(f"Checked API key envs: {', '.join(ENV_API_KEY)}", file=sys.stderr)
        print(f"Checked base URL envs: {', '.join(ENV_BASE_URL)}", file=sys.stderr)
        print(f"Checked model envs: {', '.join(ENV_MODEL)}", file=sys.stderr)
        return 2

    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(
        connect=args.connect_timeout,
        read=args.read_timeout,
        write=args.write_timeout,
        pool=args.pool_timeout,
    )
    proxies = {
        "HTTP_PROXY": os.getenv("HTTP_PROXY") or os.getenv("http_proxy"),
        "HTTPS_PROXY": os.getenv("HTTPS_PROXY") or os.getenv("https_proxy"),
    }
    print("Configuration:")
    print(f"  base_url={args.base_url}")
    print(f"  chat_url={chat_url(args.base_url)}")
    print(f"  model={args.model}")
    print(f"  rounds={args.rounds}, payload_mb={args.payload_mb}")
    print(f"  long_rounds={args.long_rounds}, long_max_tokens={args.long_max_tokens}")
    print(f"  timeouts connect/read/write/pool={args.connect_timeout}/{args.read_timeout}/{args.write_timeout}/{args.pool_timeout}s")
    print(f"  proxy HTTP_PROXY={'set' if proxies['HTTP_PROXY'] else 'unset'} HTTPS_PROXY={'set' if proxies['HTTPS_PROXY'] else 'unset'}")

    if args.matrix:
        return run_matrix(args)

    results: list[Result] = []
    large_text = random_text(int(args.payload_mb * 1024 * 1024))
    img_url = image_data_url(args.image) if args.image else None
    url = chat_url(args.base_url)

    with httpx.Client(timeout=timeout, trust_env=True, http2=args.http2) as client:
        if not args.no_models_preflight:
            try:
                start = time.perf_counter()
                response = client.get(models_url(args.base_url), headers=headers)
                print(f"/models preflight status={response.status_code} latency={time.perf_counter() - start:.2f}s")
            except Exception as exc:  # noqa: BLE001
                print(f"/models preflight failed: {type(exc).__name__}: {exc}")
                traceback.print_exc()

        for i in range(1, args.rounds + 1):
            payload = make_large_payload(args, large_text, img_url)
            results.append(run_request(client=client, url=url, headers=headers, payload=payload, phase="large", index=i))

        for i in range(1, args.long_rounds + 1):
            payload = make_long_payload(args)
            results.append(run_request(client=client, url=url, headers=headers, payload=payload, phase="long", index=i))

    print_report(results)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
