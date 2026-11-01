# Example: take a screenshot and find a button
import sys
sys.path.insert(0, ".")
import agentseeai

img, msg = agentseeai.screenshot()
print(msg)

img, msg = agentseeai.find_text("Submit")
print(msg)

# Click it if found
img, msg = agentseeai.click_text("Submit")
print(msg)