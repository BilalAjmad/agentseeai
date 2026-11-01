#!/usr/bin/env python3
"""Optional agent for people without an AI agent. Uses any OpenAI-compatible server
(local Ollama, LM Studio, vLLM, etc.) to drive Agentseeai via its CLI.

Usage:
    python agentseeai_agent.py --base-url http://localhost:11434/v1 --model llama3.2
    python agentseeai_agent.py --base-url http://localhost:8000/v1 --model qwen2.5-vl

Requires: pip install openai
"""
import argparse, json, os, subprocess, sys, time

try:
    from openai import OpenAI
except ImportError:
    print("Install: pip install openai")
    sys.exit(1)

SYSTEM = """You are a computer-use agent. You control a desktop via Agentseeai CLI.
You see screenshots (paths given). Decide the next single action.
Respond ONLY with a JSON object: {"action": "<cli command>", "args": [...], "done": bool}.
Available: screenshot, click x y, move x y, type text, key combo, scroll n, wait n, read_screen, find text, click_text text.
When task is complete set done=true with action null."""


def run_cli(*args):
    r = subprocess.run(["agentseeai", *args], capture_output=True, text=True)
    return r.stdout + r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1"))
    ap.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "llama3.2"))
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "none"))
    ap.add_argument("--task", required=True)
    ap.add_argument("--max-steps", type=int, default=30)
    args = ap.parse_args()

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    history = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Task: " + args.task}]

    for step in range(args.max_steps):
        out = run_cli("shot")
        print(out)
        # find screenshot path
        shot_path = None
        for line in out.splitlines():
            if line.startswith("screenshot:"):
                shot_path = line.split(":", 1)[1].strip()
        history.append({"role": "user", "content": f"Step {step}. Last output:\n{out}\nScreenshot: {shot_path}"})

        resp = client.chat.completions.create(model=args.model, messages=history, temperature=0.2)
        content = resp.choices[0].message.content.strip()
        print("AGENT:", content)
        try:
            decision = json.loads(content)
        except json.JSONDecodeError:
            print("bad json, stopping")
            break
        if decision.get("done"):
            print("DONE")
            break
        action = decision.get("action")
        aargs = decision.get("args", [])
        if action:
            print("RUNNING:", action, aargs)
            print(run_cli(action, *aargs))
        history.append({"role": "assistant", "content": content})
        time.sleep(0.5)


if __name__ == "__main__":
    main()