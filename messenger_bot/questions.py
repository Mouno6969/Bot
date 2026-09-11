"""Quiz question bank for the group quiz game.

Questions are deterministic local data: picking one never costs an API call and
always works offline. Each question has a category, a prompt, four options, the
index of the correct option, and an optional fun fact revealed with the result.
Add new entries at the end; the quiz picker tracks recently used indexes so the
same question does not repeat until the bank rotates.
"""

from __future__ import annotations

from dataclasses import dataclass
import random


@dataclass(frozen=True)
class Question:
    category: str
    prompt: str
    options: tuple[str, str, str, str]
    answer_index: int  # 0-based index into options
    fact: str = ""


QUESTION_BANK: tuple[Question, ...] = (
    # --- science -------------------------------------------------------------
    Question("science", "Pani (water)-er chemical formula ki?",
             ("CO2", "H2O", "O2", "NaCl"), 1,
             "H2O mane 2 ta hydrogen ar 1 ta oxygen atom."),
    Question("science", "Solar system e mot koyta planet ache?",
             ("7", "8", "9", "10"), 1),
    Question("science", "Alor speed (speed of light) koto?",
             ("3,000 km/s", "30,000 km/s", "300,000 km/s", "3,000,000 km/s"), 2),
    Question("science", "Gachpala photosynthesis korar shomoy kon gas shoson kore?",
             ("Oxygen", "Nitrogen", "Carbon dioxide", "Hydrogen"), 2),
    Question("science", "Sobcheye boro planet konta?",
             ("Earth", "Mars", "Saturn", "Jupiter"), 3),
    Question("science", "Kon planet ke 'Red Planet' bola hoy?",
             ("Mars", "Venus", "Mercury", "Neptune"), 0),
    Question("science", "Cell-er 'powerhouse' kake bole?",
             ("Nucleus", "Ribosome", "Mitochondria", "Chloroplast"), 2),
    Question("science", "Shurjo asole ki?",
             ("Ekta planet", "Ekta star", "Ekta comet", "Ekta moon"), 1),
    Question("science", "Shurjer alo theke amader shorire kon vitamin toiri hoy?",
             ("Vitamin A", "Vitamin B12", "Vitamin C", "Vitamin D"), 3),
    Question("science", "Pani koto degree Celsius e futte shuru kore?",
             ("50°C", "80°C", "100°C", "120°C"), 2),
    # --- bangladesh ----------------------------------------------------------
    Question("bangladesh", "Bangladesher rajdhani kon shohor?",
             ("Chattogram", "Dhaka", "Khulna", "Sylhet"), 1),
    Question("bangladesh", "Jatiyo shongeet 'Amar Shonar Bangla' ke likhechen?",
             ("Kazi Nazrul Islam", "Jibanananda Das", "Rabindranath Tagore", "Sukanta Bhattacharya"), 2),
    Question("bangladesh", "Bangladesh koto shale shadhinota loy?",
             ("1947", "1952", "1971", "1975"), 2),
    Question("bangladesh", "Bijoy Dibosh koto tarikh palon kora hoy?",
             ("26 March", "16 December", "21 February", "14 April"), 1),
    Question("bangladesh", "Bangladesher jatiyo ful konta?",
             ("Golap", "Shapla (water lily)", "Joba", "Beli"), 1),
    Question("bangladesh", "Bangladesher jatiyo fol konta?",
             ("Aam", "Kathal (jackfruit)", "Lichu", "Jam"), 1),
    Question("bangladesh", "Prithibir shobcheye boro sea beach konta?",
             ("Kuakata", "Cox's Bazar", "Goa", "Bali"), 1),
    Question("bangladesh", "Bangladesher currency-r naam ki?",
             ("Rupee", "Taka", "Rupiah", "Ringgit"), 1),
    Question("bangladesh", "Ekushey February kisher jonno porichito?",
             ("Cricket jit", "Bhasha Andolon (Language Movement)", "Bijoy Dibosh", "Noboborsho"), 1),
    Question("bangladesh", "Sundarbans kon jinisher jonno shobcheye famous?",
             ("Tea garden", "Royal Bengal Tiger", "Hills", "River port"), 1),
    # --- sports --------------------------------------------------------------
    Question("sports", "FIFA World Cup koto bochor por por hoy?",
             ("2", "3", "4", "5"), 2),
    Question("sports", "Bangladesh cricket team-er nickname ki?",
             ("Lions", "Tigers", "Panthers", "Sharks"), 1),
    Question("sports", "Olympic logo-te koyta ring thake?",
             ("4", "5", "6", "7"), 1),
    Question("sports", "Football match e ekta team e highest koto jon player mathe thake?",
             ("9", "10", "11", "12"), 2),
    Question("sports", "Cricket e 'LBW' er full form ki?",
             ("Long Ball Win", "Leg Before Wicket", "Left Behind Wicket", "Low Bounce Wide"), 1),
    Question("sports", "Sobcheye beshi FIFA World Cup (men) kon desh jiteche?",
             ("Argentina", "Germany", "Brazil", "France"), 2),
    Question("sports", "Tennis e 'love' mane ki score?",
             ("0", "15", "30", "40"), 0),
    Question("sports", "T20 cricket e ekta innings e koto over thake?",
             ("10", "20", "50", "60"), 1),
    # --- general -------------------------------------------------------------
    Question("general", "Prithibir sobcheye boro ocean konta?",
             ("Atlantic", "Indian", "Pacific", "Arctic"), 2),
    Question("general", "Prithibir sobcheye unchu (tallest) pahar konta?",
             ("K2", "Mount Everest", "Kangchenjunga", "Makalu"), 1),
    Question("general", "Leap year e koto din thake?",
             ("364", "365", "366", "367"), 2),
    Question("general", "Ramdhanu (rainbow) te traditionally koyta rong thake?",
             ("5", "6", "7", "8"), 2),
    Question("general", "Pyramid kon deshe famous?",
             ("Brazil", "Egypt", "India", "Japan"), 1),
    Question("general", "Sobcheye boro mammal konta?",
             ("Elephant", "Blue whale", "Giraffe", "Hippo"), 1),
    Question("general", "Fastest land animal konta?",
             ("Lion", "Horse", "Cheetah", "Deer"), 2),
    Question("general", "Adult human body te koto ta bone thake?",
             ("106", "206", "306", "416"), 1),
    Question("general", "Makorshar (spider) koyta pa thake?",
             ("6", "8", "10", "12"), 1),
    Question("general", "Octopus er koyta heart thake?",
             ("1", "2", "3", "4"), 2),
    # --- fun -----------------------------------------------------------------
    Question("fun", "'Never Gonna Give You Up' gaan giye shobai ke 'rickroll' koren ke?",
             ("Michael Jackson", "Rick Astley", "Elton John", "Bryan Adams"), 1),
    Question("fun", "Tomato asole ki?",
             ("Vegetable", "Fruit (botanical)", "Root", "Seed"), 1),
    Question("fun", "Properly stored honey koto din porjonto thik thake?",
             ("1 bochor", "10 bochor", "100 bochor", "Practically kokhono noshto hoy na"), 3),
    Question("fun", "Biral er bachcha ke ki bole?",
             ("Puppy", "Kitten", "Cub", "Joey"), 1),
    Question("fun", "Ekta standard dice er opposite side gulo jog korle koto hoy?",
             ("6", "7", "8", "9"), 1,
             "1+6, 2+5, 3+4 — shob jora 7 hoy."),
)


def pick_question(
    rng: random.Random, used: list[int], category: str = "", retry_after: int = 12
) -> tuple[int, Question]:
    """Return (bank_index, question) avoiding the most recently used indexes.

    ``category`` is matched case-insensitively; an unknown/empty category picks
    from the whole bank. When the eligible pool has been fully used, the used
    list is trimmed so old questions become available again (rotation).
    """
    pool = [
        index
        for index, question in enumerate(QUESTION_BANK)
        if not category or question.category.casefold() == category.casefold()
    ]
    if not pool:
        pool = list(range(len(QUESTION_BANK)))
    fresh = [index for index in pool if index not in used[-retry_after:]]
    if not fresh:
        # The pool rotated through everything: keep only the immediate last few
        # so the same question does not come back instantly.
        del used[:-retry_after // 3]
        fresh = [index for index in pool if index not in used[-3:]] or pool
    index = rng.choice(fresh)
    used.append(index)
    return index, QUESTION_BANK[index]


def available_categories() -> list[str]:
    return sorted({question.category for question in QUESTION_BANK})
