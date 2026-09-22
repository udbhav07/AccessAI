import google.generativeai as genai
import PIL.Image
import io, os
from dotenv import load_dotenv

from . import PROJECT_ROOT, nethttp

# Images come from whatever page the user pointed us at, so a 10KB file that
# decodes to gigabytes is a fetch away. Pillow only warns past its own default;
# this makes an oversized image refuse to decode instead.
PIL.Image.MAX_IMAGE_PIXELS = 40_000_000     # ~8000x5000
MAX_IMAGE_BYTES = 8_000_000

# The key file lives at the project root, beside app.py -- resolve it
# absolutely so the working directory does not matter.
load_dotenv(os.path.join(PROJECT_ROOT, "googleapikey.env"))
genai.configure(api_key=os.getenv("GEMAPI"))
model = genai.GenerativeModel("gemini-1.5-flash")

def getAlt(src):
    """Describe an image, or return None meaning "leave this one alone".

    None rather than a placeholder string: writing "Image description not
    available" into alt makes a screen reader announce that sentence for every
    image, which is worse than no alt at all -- and the coverage check counts
    any non-empty alt as a success, so a total API outage used to report 4/4.
    Same reasoning as getLabel returning 'y'.
    """
    try:
        response = nethttp.get(src, max_bytes=MAX_IMAGE_BYTES)
        if not response.headers.get("content-type", "").lower().startswith("image/"):
            return None

        data = response.content
        # verify() consumes the file object, so the image has to be reopened
        # before it can actually be read -- that is Pillow's API, not a slip.
        PIL.Image.open(io.BytesIO(data)).verify()
        image = PIL.Image.open(io.BytesIO(data))

        response = model.generate_content(["Give alt for this image in less than five words in square brackets", image])

        return response.text.split("[")[1].split("]")[0]

    except Exception as e:
        print(f"getAlt failed for {src}: {e}")
        return None

def is_suitable_label(label, inp):
    isSuitable = model.generate_content(f"give answers in square brackets, Give True if current label is a suitable label to the input field else give False, Label: {label}; input: {inp}; ")
    return isSuitable.text.split("[")[1].split("]")[0] == "True"

def getLabel(inp, label=""):
    try:
        if label and is_suitable_label(label, inp):
            return 'y'

        prompt = f"give answer in square brackets generate a one or two word label for the input field: {inp}"
        response = model.generate_content(prompt)
        return response.text.split("[")[1].split("]")[0]

    except Exception as e:
        print(f"getLabel failed: {e}")
        # 'y' means "leave this input alone". Returning "" would blank an
        # existing good label -- an API hiccup would actively make the page
        # worse. Same class of bug as getColors returning None.
        return 'y'

def getColors(style):
    try:
        prompt = f"Given the inline style of an element in HTML, give a modified style with text color for the element based on background color, give answer inside square brackets: {style}"
        response = model.generate_content(prompt)

        return response.text.split('[')[1].split("]")[0]

    except Exception as e:
        print(e)
        return style        # never wipe the element's styling on failure


def suggest_text_color(fg, bg):
    """Suggest an accessible text colour for `fg` sitting on `bg`.

    Returns a single hex colour so only the `color` property has to be
    touched -- asking for a whole style string back risks the model quietly
    dropping padding, font-size and everything else. Returns None on any
    failure so the caller can fall back to a deterministic pick; the answer
    is verified against the WCAG threshold by the caller either way.
    """
    try:
        prompt = (
            "Pick an accessible text colour for a web page. "
            f"The background colour is {bg} and the current text colour is {fg}. "
            "The replacement must keep the original hue as far as possible and must "
            "reach a WCAG contrast ratio of at least 4.5:1 against that background. "
            "Answer with only a hex colour inside square brackets, like [#1a1a1a]."
        )
        response = model.generate_content(prompt)
        return response.text.split("[")[1].split("]")[0].strip()

    except Exception as e:
        print(f"suggest_text_color failed: {e}")
        return None

