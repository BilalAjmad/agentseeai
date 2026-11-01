# Example: open Notepad and type text
import sys, time
sys.path.insert(0, ".")
import agentseeai

img, msg = agentseeai.open_app("notepad")
print(msg)
time.sleep(2)

img, msg = agentseeai.type_text("Hello from agentseeai!")
print(msg)

img, msg = agentseeai.key("ctrl+a")
print(msg)