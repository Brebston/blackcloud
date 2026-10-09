// Набір емоджі для вибору в чаті. Без зовнішніх бібліотек і CDN (CSP їх забороняє).
// Формат: "емоджі ключові слова" — слова українською та англійською для пошуку.

import type { TKey } from "../i18n";

export interface EmojiCategory {
  id: string;
  /** Ключ перекладу назви категорії */
  label: TKey;
  icon: string;
  items: { e: string; k: string }[];
}

function parse(src: string) {
  return src
    .trim()
    .split("\n")
    .map((line) => {
      const [e, ...rest] = line.trim().split(" ");
      return { e, k: rest.join(" ").toLowerCase() };
    });
}

export const QUICK_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🙏"];

export const EMOJI_CATEGORIES: EmojiCategory[] = [
  {
    id: "smileys",
    label: "emoji.cat.smileys",
    icon: "😀",
    items: parse(`
😀 усмішка радість smile grin happy
😃 усмішка радість smile happy
😄 сміх радість smile laugh
😁 посмішка зуби grin
😆 сміх laugh
😅 піт полегшення sweat relief
🤣 регіт rofl laugh
😂 сльози сміх joy laugh lol
🙂 усмішка slight smile
🙃 догори upside down
😉 підморгування wink
😊 рум'янець blush smile
😇 ангел angel halo
🥰 закоханий love hearts
😍 закоханий очі серця heart eyes love
🤩 зірки захват star struck
😘 поцілунок kiss
😗 поцілунок kiss
😚 поцілунок kiss
😋 смачно yum tasty
😛 язик tongue
😜 язик підморгування wink tongue
🤪 божевільний crazy zany
😝 язик tongue
🤑 гроші money
🤗 обійми hug
🤭 ой oops giggle
🤫 тихо shh quiet
🤔 думаю think hmm
🤐 мовчу zip
🤨 підозра raised eyebrow
😐 нейтрально neutral
😑 без емоцій expressionless
😶 без рота no mouth
😏 ухмилка smirk
😒 незадоволений unamused
🙄 очі закотив eye roll
😬 гримаса grimace
😮‍💨 видих exhale
🤥 брехня lie
😌 полегшення relieved
😔 сум pensive
😪 сонний sleepy
🤤 слина drool
😴 сон sleep
😷 маска хворий mask sick
🤒 температура sick fever
🤕 травма hurt
🤢 нудота nausea
🤮 блювота vomit
🤧 чхання sneeze
🥵 спека hot
🥶 холод cold
🥴 п'яний woozy
😵 запаморочення dizzy
🤯 вибух мозку mind blown
🤠 ковбой cowboy
🥳 свято вечірка party celebrate
🥸 маскування disguise
😎 круто окуляри cool sunglasses
🤓 ботан nerd
🧐 монокль monocle
😕 збентежений confused
🫤 сумнів diagonal mouth
😟 хвилювання worried
🙁 сумно frown
😮 здивування wow surprised
😯 здивування hushed
😲 шок astonished
😳 збентеження flushed
🥺 прохання pleading
🥹 зворушення tears
😦 жах frowning
😧 тривога anguished
😨 страх fear
😰 тривога anxious
😥 сум sad relieved
😢 сльоза плач cry sad
😭 ридання sob cry
😱 крик жах scream
😖 розпач confounded
😣 терпіння persevere
😞 розчарування disappointed
😓 піт sweat
😩 втома weary
😫 втомлений tired
🥱 позіхання yawn
😤 злість тріумф huff
😡 злий angry rage
😠 злий angry
🤬 лайка cursing
😈 диявол devil
👿 диявол imp
💀 череп skull dead
☠️ череп skull
💩 какашка poop
🤡 клоун clown
👻 привид ghost
👽 прибулець alien
🤖 робот robot
😺 кіт cat smile
😹 кіт сміх cat joy
😻 кіт любов cat love
🙈 мавпа не бачу see no evil
🙉 мавпа не чую hear no evil
🙊 мавпа мовчу speak no evil
🫠 тану melting
`),
  },
  {
    id: "gestures",
    label: "emoji.cat.gestures",
    icon: "👍",
    items: parse(`
👍 лайк так добре like thumbs up yes ok
👎 дизлайк ні dislike thumbs down no
👌 окей ok
🤌 жест pinched
🤏 трохи pinch
✌️ перемога мир victory peace
🤞 удачі fingers crossed luck
🫰 серце пальцями finger heart
🤟 любов love you
🤘 рок rock
🤙 подзвони call me
👈 ліворуч left
👉 праворуч right
👆 вгору up
👇 вниз down
☝️ вказівний point up
👋 привіт бувай wave hello bye
🤚 рука hand
🖐️ п'ять пальців hand
✋ стоп stop hand
🖖 вулкан vulcan
👏 оплески clap bravo
🙌 ура hooray raise hands
🫶 серце руками heart hands
👐 відкриті руки open hands
🤲 долоні palms
🤝 рукостискання домовились handshake deal
🙏 дякую будь ласка молитва pray thanks please
✍️ пишу write
💪 сила біцепс strong muscle
🦾 протез mechanical arm
👀 очі дивлюсь eyes look
🧠 мозок brain
🫡 салют salute
🤷 не знаю shrug
🙋 я тут raise hand
🙅 ні no
🙆 так ok
🤦 фейспалм facepalm
🙇 уклін bow
💁 інфо tipping hand
`),
  },
  {
    id: "hearts",
    label: "emoji.cat.hearts",
    icon: "❤️",
    items: parse(`
❤️ серце любов heart love red
🧡 помаранчеве серце orange heart
💛 жовте серце yellow heart
💚 зелене серце green heart
💙 синє серце blue heart
💜 фіолетове серце purple heart
🖤 чорне серце black heart
🤍 біле серце white heart
🤎 коричневе серце brown heart
💔 розбите серце broken heart
❤️‍🔥 палке серце heart fire
❤️‍🩹 загоєне серце mending heart
💕 два серця two hearts
💞 серця hearts
💓 серцебиття heartbeat
💗 серце growing heart
💖 блискуче серце sparkling heart
💘 стріла amor cupid
💝 подарунок серце gift heart
💯 сто відсотків hundred
💥 бум boom
💫 зірочки dizzy
💦 краплі splash
💨 швидко dash
🔥 вогонь круто fire hot lit
✨ блиск іскри sparkles
⭐ зірка star
🌟 зірка сяйво glowing star
⚡ блискавка lightning
🎉 свято вітаю party popper congrats
🎊 конфеті confetti
`),
  },
  {
    id: "animals",
    label: "emoji.cat.animals",
    icon: "🐱",
    items: parse(`
🐶 собака dog
🐱 кіт cat
🐭 миша mouse
🐹 хом'як hamster
🐰 кролик rabbit
🦊 лисиця fox
🐻 ведмідь bear
🐼 панда panda
🐨 коала koala
🐯 тигр tiger
🦁 лев lion
🐮 корова cow
🐷 свиня pig
🐸 жаба frog
🐵 мавпа monkey
🐔 курка chicken
🐧 пінгвін penguin
🐦 птах bird
🦆 качка duck
🦅 орел eagle
🦉 сова owl
🐺 вовк wolf
🐴 кінь horse
🦄 єдиноріг unicorn
🐝 бджола bee
🦋 метелик butterfly
🐌 равлик snail
🐞 сонечко ladybug
🐢 черепаха turtle
🐍 змія snake
🐙 восьминіг octopus
🐬 дельфін dolphin
🐳 кит whale
🦈 акула shark
🌸 квітка сакура blossom
🌹 троянда rose
🌻 соняшник sunflower
🌷 тюльпан tulip
🌱 паросток seedling
🌲 ялинка tree
🌴 пальма palm
🍀 конюшина удача clover luck
🍁 клен maple
☀️ сонце sun
🌙 місяць moon
🌈 веселка rainbow
☁️ хмара cloud
🌧️ дощ rain
❄️ сніг snow
⛄ сніговик snowman
🌊 хвиля wave sea
`),
  },
  {
    id: "food",
    label: "emoji.cat.food",
    icon: "🍕",
    items: parse(`
🍏 яблуко apple
🍎 яблуко apple
🍐 груша pear
🍊 апельсин orange
🍋 лимон lemon
🍌 банан banana
🍉 кавун watermelon
🍇 виноград grapes
🍓 полуниця strawberry
🍒 вишня cherry
🍑 персик peach
🥭 манго mango
🍍 ананас pineapple
🥥 кокос coconut
🥝 ківі kiwi
🍅 помідор tomato
🥑 авокадо avocado
🥕 морква carrot
🌽 кукурудза corn
🥔 картопля potato
🍞 хліб bread
🧀 сир cheese
🥚 яйце egg
🍳 яєчня egg cooking
🥞 млинці pancakes
🥓 бекон bacon
🍔 бургер burger
🍟 картопля фрі fries
🍕 піца pizza
🌭 хотдог hotdog
🌮 тако taco
🍝 паста pasta
🍜 суп локшина ramen
🍣 суші sushi
🥟 вареники пельмені dumpling
🍰 торт cake
🎂 день народження торт birthday cake
🧁 капкейк cupcake
🍫 шоколад chocolate
🍬 цукерка candy
🍩 пончик donut
🍪 печиво cookie
☕ кава coffee
🍵 чай tea
🍺 пиво beer
🍻 тост пиво cheers beer
🥂 келихи тост cheers
🍷 вино wine
🥃 віскі whisky
🧃 сік juice
`),
  },
  {
    id: "activity",
    label: "emoji.cat.activity",
    icon: "⚽",
    items: parse(`
⚽ футбол soccer
🏀 баскетбол basketball
🏈 регбі football
⚾ бейсбол baseball
🎾 теніс tennis
🏐 волейбол volleyball
🏓 пінг-понг ping pong
🥊 бокс boxing
🏋️ штанга gym
🚴 велосипед bike
🏃 біг run
🧘 йога yoga
🏊 плавання swim
⛷️ лижі ski
🏆 кубок перемога trophy win
🥇 перше місце gold medal
🥈 друге місце silver
🥉 третє місце bronze
🎮 ігри game
🎲 кубик dice
🎯 ціль target
🎨 малювання art
🎬 кіно movie
🎤 мікрофон karaoke
🎧 навушники headphones
🎵 музика music
🎸 гітара guitar
🎹 піаніно piano
🎁 подарунок gift
🎈 кулька balloon
🎄 ялинка christmas
🎃 гарбуз halloween
`),
  },
  {
    id: "travel",
    label: "emoji.cat.travel",
    icon: "✈️",
    items: parse(`
🚗 авто car
🚕 таксі taxi
🚌 автобус bus
🚎 тролейбус trolleybus
🚑 швидка ambulance
🚒 пожежна fire truck
🚓 поліція police
🚲 велосипед bicycle
🛴 самокат scooter
🚂 потяг train
🚆 потяг train
🚇 метро metro
✈️ літак plane flight
🚀 ракета rocket launch
🛸 нло ufo
🚢 корабель ship
⛵ вітрильник sailboat
⚓ якір anchor
🗺️ мапа map
🧭 компас compass
🏔️ гори mountain
🏕️ кемпінг camping
🏖️ пляж beach
🏝️ острів island
🏠 дім home house
🏢 офіс office
🏥 лікарня hospital
🏫 школа school
🏰 замок castle
⛪ церква church
🌍 земля world
🌃 ніч місто night city
🌅 світанок sunrise
`),
  },
  {
    id: "objects",
    label: "emoji.cat.objects",
    icon: "💡",
    items: parse(`
💡 ідея idea bulb
📱 телефон phone
💻 ноутбук laptop
🖥️ комп'ютер computer
⌨️ клавіатура keyboard
🖱️ миша mouse
🖨️ принтер printer
📷 фото camera
🎥 відео camera
📺 телевізор tv
⏰ будильник alarm
⌛ час hourglass
📅 календар calendar
📌 закріпити pin
📎 скріпка paperclip
✏️ олівець pencil
📝 нотатка memo note
📄 документ document
📁 папка folder
📦 посилка package box
✉️ лист envelope mail
📧 пошта email
🔒 замок secure lock
🔓 відкрито unlock
🔑 ключ key
🛡️ захист shield
🔧 ключ ремонт wrench
🔨 молоток hammer
⚙️ налаштування gear settings
🧰 інструменти toolbox
💊 таблетка pill
💰 гроші money bag
💳 картка card
📈 зростання chart up
📉 падіння chart down
📊 графік chart
🔔 дзвінок bell
📣 оголошення megaphone
🔍 пошук search
🗑️ кошик trash
`),
  },
  {
    id: "symbols",
    label: "emoji.cat.symbols",
    icon: "✅",
    items: parse(`
✅ так готово done check yes
☑️ позначка check box
✔️ галочка check
❌ ні хрест no cross
❎ хрест cross
❗ увага exclamation
❓ питання question
‼️ увага bangbang
⁉️ що interrobang
⚠️ попередження warning
🚫 заборонено forbidden
⛔ стоп no entry
🔴 червоний red circle
🟠 помаранчевий orange circle
🟡 жовтий yellow circle
🟢 зелений green circle
🔵 синій blue circle
🟣 фіолетовий purple circle
⚫ чорний black circle
⚪ білий white circle
⬆️ вгору up
⬇️ вниз down
⬅️ ліворуч left
➡️ праворуч right
🔄 оновити refresh
➕ плюс plus
➖ мінус minus
➗ ділення divide
✖️ множення multiply
♾️ нескінченність infinity
💲 долар dollar
©️ копірайт copyright
®️ зареєстровано registered
™️ торгова марка trademark
🆗 ок ok
🆕 нове new
🆒 круто cool
🆘 допомога sos
`),
  },
  {
    id: "flags",
    label: "emoji.cat.flags",
    icon: "🏳️",
    items: parse(`
🇺🇦 україна ukraine
🇵🇱 польща poland
🇪🇺 євросоюз eu europe
🇬🇧 британія uk
🇺🇸 сша usa
🇨🇦 канада canada
🇩🇪 німеччина germany
🇫🇷 франція france
🇮🇹 італія italy
🇪🇸 іспанія spain
🇨🇿 чехія czech
🇸🇰 словаччина slovakia
🇱🇹 литва lithuania
🇱🇻 латвія latvia
🇪🇪 естонія estonia
🇷🇴 румунія romania
🇲🇩 молдова moldova
🇬🇪 грузія georgia
🇳🇱 нідерланди netherlands
🇸🇪 швеція sweden
🇳🇴 норвегія norway
🇫🇮 фінляндія finland
🇯🇵 японія japan
🏳️ білий прапор white flag
🏴 чорний прапор black flag
🏁 фініш finish
🚩 червоний прапор red flag
🏳️‍🌈 веселка rainbow flag
`),
  },
];

const RECENT_KEY = "bc_recent_emoji";

export function recentEmoji(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string").slice(0, 24) : [];
  } catch {
    return [];
  }
}

export function rememberEmoji(e: string) {
  try {
    const list = [e, ...recentEmoji().filter((x) => x !== e)].slice(0, 24);
    localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  } catch {
    /* приватний режим тощо — не критично */
  }
}

export function searchEmoji(query: string) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const out: string[] = [];
  for (const cat of EMOJI_CATEGORIES) {
    for (const item of cat.items) {
      if (item.k.split(" ").some((w) => w.startsWith(q)) || item.k.includes(q)) out.push(item.e);
    }
  }
  return Array.from(new Set(out)).slice(0, 80);
}
