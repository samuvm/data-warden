"""Vocabularies for the CIERZO world.

Person names come from Faker, seeded, and only ever as POOLS: calling Faker once
per row would take hours at full scale, so a few thousand components are drawn
once and combined with integer indices afterwards. Everything Faker does not know
about -- merchant category codes, ISO 8583 response codes, card BIN ranges,
autonomous system names -- is written out here, because inventing a plausible-
looking MCC is exactly the kind of detail a payments engineer checks first.
"""

from __future__ import annotations

import numpy as np
from faker import Faker

# (code, name, region, currency, lat, lon, traffic weight, avg income index)
COUNTRIES: list[tuple] = [
    ("ES", "Spain", "SOUTH_EU", "EUR", 40.42, -3.70, 0.272, 0.78),
    ("FR", "France", "WEST_EU", "EUR", 48.86, 2.35, 0.121, 0.94),
    ("DE", "Germany", "WEST_EU", "EUR", 52.52, 13.40, 0.113, 1.00),
    ("IT", "Italy", "SOUTH_EU", "EUR", 41.90, 12.50, 0.084, 0.81),
    ("PT", "Portugal", "SOUTH_EU", "EUR", 38.72, -9.14, 0.052, 0.66),
    ("GB", "United Kingdom", "WEST_EU", "GBP", 51.51, -0.13, 0.108, 0.97),
    ("NL", "Netherlands", "WEST_EU", "EUR", 52.37, 4.90, 0.041, 1.05),
    ("BE", "Belgium", "WEST_EU", "EUR", 50.85, 4.35, 0.024, 0.98),
    ("IE", "Ireland", "WEST_EU", "EUR", 53.35, -6.26, 0.014, 1.09),
    ("PL", "Poland", "EAST_EU", "PLN", 52.23, 21.01, 0.031, 0.55),
    ("SE", "Sweden", "NORTH_EU", "SEK", 59.33, 18.07, 0.022, 1.03),
    ("DK", "Denmark", "NORTH_EU", "DKK", 55.68, 12.57, 0.015, 1.06),
    ("NO", "Norway", "NORTH_EU", "NOK", 59.91, 10.75, 0.012, 1.12),
    ("FI", "Finland", "NORTH_EU", "EUR", 60.17, 24.94, 0.010, 1.01),
    ("AT", "Austria", "WEST_EU", "EUR", 48.21, 16.37, 0.013, 0.99),
    ("CH", "Switzerland", "WEST_EU", "CHF", 47.38, 8.54, 0.016, 1.24),
    ("CZ", "Czechia", "EAST_EU", "CZK", 50.08, 14.44, 0.011, 0.62),
    ("HU", "Hungary", "EAST_EU", "HUF", 47.50, 19.04, 0.007, 0.53),
    ("RO", "Romania", "EAST_EU", "RON", 44.43, 26.10, 0.009, 0.48),
    ("BG", "Bulgaria", "EAST_EU", "BGN", 42.70, 23.32, 0.005, 0.44),
    ("GR", "Greece", "SOUTH_EU", "EUR", 37.98, 23.73, 0.008, 0.60),
    ("US", "United States", "AMERICAS", "USD", 38.90, -77.04, 0.024, 1.15),
    ("MX", "Mexico", "AMERICAS", "MXN", 19.43, -99.13, 0.011, 0.34),
    ("BR", "Brazil", "AMERICAS", "BRL", -23.55, -46.63, 0.009, 0.31),
    ("MA", "Morocco", "AFRICA", "EUR", 33.57, -7.59, 0.004, 0.24),
    ("TR", "Turkey", "ASIA", "EUR", 41.01, 28.98, 0.006, 0.36),
    ("SG", "Singapore", "ASIA", "USD", 1.35, 103.82, 0.003, 1.18),
    ("JP", "Japan", "ASIA", "USD", 35.68, 139.69, 0.004, 0.96),
    # Card-issuing jurisdictions with almost no card-ACCEPTING traffic. They exist
    # here because the neobank and prepaid BINs in CARD_BINS are issued from them,
    # and a foreign key with nowhere to point is a defect no matter how small the
    # weight. Their traffic share is deliberately tiny, which is also true.
    ("LT", "Lithuania", "EAST_EU", "EUR", 54.69, 25.28, 0.0026, 0.52),
    ("MT", "Malta", "SOUTH_EU", "EUR", 35.90, 14.51, 0.0018, 0.72),
]

# Cities carry the geo signal. lat/lon are real to two decimals so that a
# distance computed between two payments is a distance a reader can sanity-check
# on a map -- which is the whole point of the location side channel.
CITIES: dict[str, list[tuple]] = {
    "ES": [
        ("Madrid", 40.42, -3.70, "28"),
        ("Barcelona", 41.39, 2.17, "08"),
        ("Valencia", 39.47, -0.38, "46"),
        ("Sevilla", 37.39, -5.98, "41"),
        ("Zaragoza", 41.65, -0.89, "50"),
        ("Malaga", 36.72, -4.42, "29"),
        ("Bilbao", 43.26, -2.93, "48"),
        ("Murcia", 37.99, -1.13, "30"),
        ("Palma", 39.57, 2.65, "07"),
        ("Las Palmas", 28.12, -15.44, "35"),
        ("Vigo", 42.24, -8.72, "36"),
        ("Valladolid", 41.65, -4.72, "47"),
    ],
    "FR": [
        ("Paris", 48.86, 2.35, "75"),
        ("Lyon", 45.76, 4.84, "69"),
        ("Marseille", 43.30, 5.37, "13"),
        ("Toulouse", 43.60, 1.44, "31"),
        ("Nantes", 47.22, -1.55, "44"),
        ("Bordeaux", 44.84, -0.58, "33"),
    ],
    "DE": [
        ("Berlin", 52.52, 13.40, "10"),
        ("Munchen", 48.14, 11.58, "80"),
        ("Hamburg", 53.55, 9.99, "20"),
        ("Koln", 50.94, 6.96, "50"),
        ("Frankfurt", 50.11, 8.68, "60"),
        ("Stuttgart", 48.78, 9.18, "70"),
    ],
    "IT": [
        ("Roma", 41.90, 12.50, "00"),
        ("Milano", 45.46, 9.19, "20"),
        ("Napoli", 40.85, 14.27, "80"),
        ("Torino", 45.07, 7.69, "10"),
        ("Bologna", 44.49, 11.34, "40"),
    ],
    "PT": [
        ("Lisboa", 38.72, -9.14, "10"),
        ("Porto", 41.15, -8.61, "40"),
        ("Braga", 41.55, -8.43, "47"),
        ("Faro", 37.02, -7.93, "80"),
    ],
    "GB": [
        ("London", 51.51, -0.13, "EC"),
        ("Manchester", 53.48, -2.24, "M1"),
        ("Birmingham", 52.49, -1.89, "B1"),
        ("Edinburgh", 55.95, -3.19, "EH"),
        ("Bristol", 51.45, -2.59, "BS"),
    ],
    "NL": [
        ("Amsterdam", 52.37, 4.90, "10"),
        ("Rotterdam", 51.92, 4.48, "30"),
        ("Utrecht", 52.09, 5.12, "35"),
    ],
    "BE": [("Brussel", 50.85, 4.35, "10"), ("Antwerpen", 51.22, 4.40, "20")],
    "IE": [("Dublin", 53.35, -6.26, "D0"), ("Cork", 51.90, -8.47, "T1")],
    "PL": [
        ("Warszawa", 52.23, 21.01, "00"),
        ("Krakow", 50.06, 19.94, "30"),
        ("Wroclaw", 51.11, 17.04, "50"),
        ("Gdansk", 54.35, 18.65, "80"),
    ],
    "SE": [("Stockholm", 59.33, 18.07, "11"), ("Goteborg", 57.71, 11.97, "41")],
    "DK": [("Kobenhavn", 55.68, 12.57, "10"), ("Aarhus", 56.16, 10.20, "80")],
    "NO": [("Oslo", 59.91, 10.75, "01"), ("Bergen", 60.39, 5.32, "50")],
    "FI": [("Helsinki", 60.17, 24.94, "00"), ("Tampere", 61.50, 23.79, "33")],
    "AT": [("Wien", 48.21, 16.37, "10"), ("Graz", 47.07, 15.44, "80")],
    "CH": [("Zurich", 47.38, 8.54, "80"), ("Geneve", 46.20, 6.14, "12")],
    "CZ": [("Praha", 50.08, 14.44, "11"), ("Brno", 49.20, 16.61, "60")],
    "HU": [("Budapest", 47.50, 19.04, "10")],
    "RO": [("Bucuresti", 44.43, 26.10, "01"), ("Cluj", 46.77, 23.60, "40")],
    "BG": [("Sofia", 42.70, 23.32, "10")],
    "GR": [("Athina", 37.98, 23.73, "10"), ("Thessaloniki", 40.64, 22.94, "54")],
    "US": [
        ("New York", 40.71, -74.01, "10"),
        ("Miami", 25.76, -80.19, "33"),
        ("Chicago", 41.88, -87.63, "60"),
        ("Austin", 30.27, -97.74, "78"),
    ],
    "MX": [("Ciudad de Mexico", 19.43, -99.13, "01"), ("Guadalajara", 20.66, -103.35, "44")],
    "BR": [("Sao Paulo", -23.55, -46.63, "01"), ("Rio de Janeiro", -22.91, -43.17, "20")],
    "MA": [("Casablanca", 33.57, -7.59, "20"), ("Rabat", 34.02, -6.84, "10")],
    "TR": [("Istanbul", 41.01, 28.98, "34"), ("Ankara", 39.93, 32.86, "06")],
    "SG": [("Singapore", 1.35, 103.82, "01")],
    "JP": [("Tokyo", 35.68, 139.69, "10"), ("Osaka", 34.69, 135.50, "53")],
    "LT": [("Vilnius", 54.69, 25.28, "01"), ("Kaunas", 54.90, 23.90, "44")],
    "MT": [("Valletta", 35.90, 14.51, "VL")],
}

# Real four-digit merchant category codes. `risk` drives dispute and fraud rates;
# `ticket` is the median basket in EUR; `high_risk` is the industry term and it is
# what makes a merchant's settlement reserve and fee band different.
MCC: list[tuple] = [
    (5411, "Grocery Stores, Supermarkets", "RETAIL", 0.12, 46.0, False),
    (5812, "Eating Places, Restaurants", "FOOD", 0.22, 31.0, False),
    (5814, "Fast Food Restaurants", "FOOD", 0.18, 13.5, False),
    (5691, "Men's and Women's Clothing", "FASHION", 0.55, 78.0, False),
    (5651, "Family Clothing Stores", "FASHION", 0.52, 62.0, False),
    (5661, "Shoe Stores", "FASHION", 0.58, 89.0, False),
    (5732, "Electronics Stores", "ELECTRONICS", 0.95, 245.0, False),
    (5734, "Computer Software Stores", "ELECTRONICS", 1.20, 74.0, False),
    (5945, "Hobby, Toy and Game Shops", "LEISURE", 0.48, 41.0, False),
    (5942, "Book Stores", "LEISURE", 0.26, 27.0, False),
    (5912, "Drug Stores and Pharmacies", "HEALTH", 0.21, 34.0, False),
    (8062, "Hospitals", "HEALTH", 0.30, 620.0, False),
    (8011, "Doctors and Physicians", "HEALTH", 0.28, 95.0, False),
    (5977, "Cosmetic Stores", "BEAUTY", 0.72, 52.0, False),
    (7230, "Beauty and Barber Shops", "BEAUTY", 0.24, 38.0, False),
    (4111, "Local Commuter Transport", "TRAVEL", 0.19, 22.0, False),
    (4511, "Airlines and Air Carriers", "TRAVEL", 1.85, 318.0, True),
    (7011, "Lodging, Hotels and Resorts", "TRAVEL", 1.45, 214.0, True),
    (4722, "Travel Agencies", "TRAVEL", 2.10, 640.0, True),
    (7512, "Automobile Rental", "TRAVEL", 1.35, 275.0, True),
    (5541, "Service Stations", "AUTOMOTIVE", 0.14, 58.0, False),
    (5533, "Automotive Parts", "AUTOMOTIVE", 0.61, 112.0, False),
    (5999, "Miscellaneous Retail", "RETAIL", 0.68, 55.0, False),
    (5964, "Direct Marketing, Catalog", "RETAIL", 1.15, 68.0, False),
    (5967, "Direct Marketing, Inbound Tele", "RETAIL", 2.40, 49.0, True),
    (7995, "Betting and Wagering", "GAMBLING", 4.20, 87.0, True),
    (7994, "Video Game Arcades", "GAMING", 1.10, 24.0, False),
    (5816, "Digital Goods, Games", "GAMING", 1.55, 18.5, False),
    (5815, "Digital Goods, Media", "MEDIA", 0.88, 11.0, False),
    (4899, "Cable and Streaming Services", "MEDIA", 0.65, 14.5, False),
    (4816, "Computer Network Services", "SAAS", 0.92, 129.0, False),
    (7372, "Computer Programming, Software", "SAAS", 0.78, 340.0, False),
    (8220, "Colleges and Universities", "EDUCATION", 0.35, 890.0, False),
    (8299, "Schools and Educational Svc", "EDUCATION", 0.44, 165.0, False),
    (6012, "Financial Institutions", "FINANCIAL", 3.10, 420.0, True),
    (6051, "Quasi Cash, Crypto", "CRYPTO", 5.80, 510.0, True),
    (5122, "Drugs and Pharmaceuticals", "HEALTH", 1.95, 88.0, True),
    (5993, "Cigar Stores", "TOBACCO", 1.40, 42.0, True),
    (5921, "Package Stores, Beer, Wine", "ALCOHOL", 0.75, 39.0, False),
    (8398, "Charitable Organisations", "NONPROFIT", 0.32, 30.0, False),
    (7997, "Membership Clubs", "LEISURE", 0.85, 76.0, False),
    (5300, "Wholesale Clubs", "RETAIL", 0.29, 187.0, False),
    (5200, "Home Supply Warehouse", "HOME", 0.41, 134.0, False),
    (5712, "Furniture and Home Furnishings", "HOME", 0.79, 298.0, False),
    (5722, "Household Appliance Stores", "HOME", 0.83, 356.0, False),
]

# Product taxonomy: (category, subcategory, [noun templates], price low, price high,
# SALES FREQUENCY). The last field is the one that took a second pass: without it
# the catalogue held as many catering trays as lunch menus, and the average food
# ticket came out at 625 EUR. Cheap things are bought far more often than
# expensive ones, and a catalogue reflects that in how many SKUs it carries.
# The affinity between a customer segment and a category is what makes a profiling
# query find real signal instead of noise -- see `dims.customer_traits`.
PRODUCT_TAXONOMY: list[tuple] = [
    ("ELECTRONICS", "Smartphones", ["Smartphone", "Phone", "Handset"], 189, 1290, 0.6),
    ("ELECTRONICS", "Laptops", ["Laptop", "Notebook", "Ultrabook"], 399, 2890, 0.4),
    ("ELECTRONICS", "Audio", ["Headphones", "Earbuds", "Speaker", "Soundbar"], 19, 590, 2.2),
    ("ELECTRONICS", "Wearables", ["Smartwatch", "Fitness Band", "Tracker"], 29, 749, 1.1),
    ("ELECTRONICS", "Cameras", ["Camera", "Lens", "Gimbal", "Tripod"], 59, 3200, 0.3),
    ("ELECTRONICS", "Accessories", ["Cable", "Charger", "Case", "Adapter", "Hub"], 4, 89, 5.4),
    ("FASHION", "Womenswear", ["Dress", "Blouse", "Skirt", "Coat", "Jumpsuit"], 15, 380, 3.0),
    ("FASHION", "Menswear", ["Shirt", "Trousers", "Jacket", "Polo", "Chinos"], 15, 420, 2.6),
    ("FASHION", "Footwear", ["Sneakers", "Boots", "Loafers", "Sandals"], 25, 490, 1.8),
    ("FASHION", "Bags", ["Backpack", "Tote", "Crossbody", "Wallet"], 18, 890, 1.2),
    ("FASHION", "Kidswear", ["T-Shirt", "Leggings", "Pyjamas", "Hoodie"], 8, 79, 1.9),
    ("HOME", "Kitchen", ["Blender", "Kettle", "Pan Set", "Coffee Machine"], 12, 690, 1.6),
    ("HOME", "Furniture", ["Chair", "Desk", "Shelf", "Sofa", "Table"], 39, 1890, 0.5),
    ("HOME", "Bedding", ["Duvet", "Pillow", "Sheet Set", "Mattress"], 15, 890, 0.9),
    ("HOME", "Decor", ["Lamp", "Rug", "Mirror", "Vase", "Frame"], 7, 349, 2.4),
    ("HOME", "Tools", ["Drill", "Toolkit", "Ladder", "Sander"], 19, 449, 1.1),
    ("BEAUTY", "Skincare", ["Serum", "Moisturiser", "Cleanser", "Sunscreen"], 8, 189, 2.6),
    ("BEAUTY", "Fragrance", ["Eau de Parfum", "Cologne", "Body Mist"], 19, 240, 1.0),
    ("BEAUTY", "Haircare", ["Shampoo", "Conditioner", "Hair Oil", "Dryer"], 5, 199, 2.4),
    ("BEAUTY", "Makeup", ["Lipstick", "Foundation", "Palette", "Mascara"], 6, 89, 2.7),
    ("GROCERY", "Pantry", ["Olive Oil", "Pasta", "Rice", "Coffee", "Cereal"], 1, 39, 6.0),
    ("GROCERY", "Fresh", ["Cheese", "Ham", "Salmon", "Vegetables"], 2, 45, 5.2),
    ("GROCERY", "Beverages", ["Wine", "Beer", "Juice", "Sparkling Water"], 1, 120, 3.4),
    ("GROCERY", "Household", ["Detergent", "Cleaner", "Paper Towels"], 2, 29, 3.0),
    ("LEISURE", "Books", ["Novel", "Cookbook", "Atlas", "Biography"], 6, 65, 2.2),
    ("LEISURE", "Toys", ["Building Set", "Puzzle", "Board Game", "Doll"], 5, 199, 1.7),
    (
        "LEISURE",
        "Sports",
        ["Yoga Mat", "Dumbbell", "Running Shoes", "Bike Helmet"],
        9,
        590,
        1.4,
    ),
    ("LEISURE", "Outdoor", ["Tent", "Sleeping Bag", "Trekking Poles"], 19, 690, 0.6),
    ("MEDIA", "Streaming", ["Monthly Pass", "Annual Pass", "Family Plan"], 4, 180, 4.2),
    ("MEDIA", "Music", ["Vinyl", "Album Download", "Concert Ticket"], 9, 240, 1.1),
    ("GAMING", "Games", ["Game Key", "Season Pass", "Expansion"], 5, 89, 2.6),
    ("GAMING", "In-Game", ["Coin Pack", "Skin Bundle", "Battle Pass"], 1, 99, 4.4),
    ("GAMING", "Hardware", ["Controller", "Headset", "Mouse", "Keyboard"], 19, 349, 0.7),
    ("SAAS", "Subscriptions", ["Starter Plan", "Pro Plan", "Team Seat"], 9, 990, 2.8),
    ("SAAS", "Add-ons", ["Extra Storage", "API Quota", "Priority Support"], 5, 490, 1.4),
    ("TRAVEL", "Flights", ["Economy Fare", "Flex Fare", "Business Fare"], 39, 2400, 0.7),
    ("TRAVEL", "Stays", ["Night Stay", "Weekend Package", "Suite Upgrade"], 45, 890, 0.9),
    (
        "TRAVEL",
        "Extras",
        ["Checked Bag", "Seat Selection", "Insurance", "Transfer"],
        6,
        180,
        2.6,
    ),
    ("HEALTH", "Pharmacy", ["Vitamin Pack", "Supplement", "First Aid Kit"], 4, 89, 4.0),
    ("HEALTH", "Devices", ["Thermometer", "Blood Pressure Monitor", "Scale"], 15, 240, 0.7),
    ("AUTOMOTIVE", "Parts", ["Brake Pads", "Wiper Blades", "Battery", "Filter"], 9, 490, 1.2),
    ("AUTOMOTIVE", "Fuel", ["Fuel Top-Up", "Charging Session"], 5, 120, 4.6),
    ("EDUCATION", "Courses", ["Course Access", "Certification", "Workshop"], 19, 1890, 1.0),
    ("NONPROFIT", "Donations", ["One-Off Donation", "Monthly Gift"], 3, 500, 1.0),
    ("RETAIL", "General", ["Assorted Goods", "Gift Card", "Store Credit"], 5, 300, 4.0),
    ("FOOD", "Menu", ["Menu del Dia", "Family Meal", "Delivery Order"], 6, 95, 6.4),
    ("FOOD", "Catering", ["Catering Tray", "Event Package"], 45, 890, 0.35),
    ("GAMBLING", "Wagering", ["Bet Slip", "Account Deposit", "Tournament Entry"], 5, 500, 3.0),
    ("FINANCIAL", "Services", ["Transfer Fee", "Account Top-Up", "Card Issuance"], 2, 900, 1.0),
    ("CRYPTO", "Quasi-Cash", ["Token Purchase", "Wallet Top-Up"], 20, 2500, 1.0),
    ("TOBACCO", "Tobacco", ["Cigar Box", "Vape Kit", "Pouch Pack"], 6, 180, 1.6),
    (
        "ALCOHOL",
        "Wine and Spirits",
        ["Wine Case", "Spirits Bottle", "Craft Beer Pack"],
        9,
        420,
        1.8,
    ),
]

# Product categories map onto merchant categories one-to-one EXCEPT for GROCERY,
# which is sold by both RETAIL and FOOD merchants. Keeping the mapping explicit
# rather than assuming the names line up is what stops a basket of smartphones
# turning up at a pharmacy.
PRODUCT_CAT_TO_MCC_CAT = {"GROCERY": ["RETAIL", "FOOD"]}

PRODUCT_ADJECTIVES = [
    "Classic",
    "Pro",
    "Lite",
    "Max",
    "Essential",
    "Premium",
    "Studio",
    "Compact",
    "Ultra",
    "Everyday",
    "Signature",
    "Original",
    "Nordic",
    "Urban",
    "Vintage",
    "Eco",
    "Advanced",
    "Core",
    "Select",
    "Heritage",
    "Active",
    "Smart",
    "Prime",
]
PRODUCT_BRANDS = [
    "Alderon",
    "Belmonte",
    "Cantera",
    "Dovela",
    "Estrada",
    "Farolet",
    "Granara",
    "Halcyon",
    "Ibarra",
    "Jarama",
    "Kestrel",
    "Lumin",
    "Montseny",
    "Nervion",
    "Ordesa",
    "Palmar",
    "Quintal",
    "Ribera",
    "Sanabria",
    "Torcal",
    "Umbria",
    "Valdivia",
    "Wexford",
    "Xanthe",
    "Yesera",
    "Zurbano",
    "Aranda",
    "Brava",
    "Cierra",
    "Duero",
    "Ebro",
    "Fresneda",
    "Gaviota",
    "Hontanar",
    "Isar",
]

# (scheme, bin prefix, issuer name, issuer country, funding type, weight)
CARD_BINS: list[tuple] = [
    ("visa", "454321", "Banco Peninsular", "ES", "debit", 0.082),
    ("visa", "451416", "Banco Peninsular", "ES", "credit", 0.054),
    ("visa", "402360", "Caja Levante", "ES", "debit", 0.061),
    ("visa", "455631", "Caja Levante", "ES", "credit", 0.028),
    ("visa", "446542", "Norwood Bank", "GB", "debit", 0.047),
    ("visa", "465901", "Norwood Bank", "GB", "credit", 0.031),
    ("visa", "497010", "Banque du Rhone", "FR", "debit", 0.043),
    ("visa", "413208", "Rheinische Bank", "DE", "credit", 0.038),
    ("visa", "428814", "Banca Ligure", "IT", "debit", 0.030),
    ("visa", "489504", "Banco do Tejo", "PT", "debit", 0.024),
    ("mastercard", "521234", "Banco Peninsular", "ES", "credit", 0.066),
    ("mastercard", "535409", "Caja Levante", "ES", "debit", 0.049),
    ("mastercard", "552901", "Norwood Bank", "GB", "credit", 0.036),
    ("mastercard", "544117", "Banque du Rhone", "FR", "credit", 0.033),
    ("mastercard", "516730", "Rheinische Bank", "DE", "debit", 0.041),
    ("mastercard", "533718", "Banca Ligure", "IT", "credit", 0.022),
    ("mastercard", "530612", "Bank Wisla", "PL", "debit", 0.019),
    ("mastercard", "548812", "Nordbanken", "SE", "credit", 0.016),
    ("mastercard", "557203", "Alpenbank", "AT", "credit", 0.011),
    ("amex", "371449", "Meridian Express", "GB", "credit", 0.021),
    ("amex", "378282", "Meridian Express", "US", "credit", 0.014),
    ("maestro", "675993", "Caja Levante", "ES", "debit", 0.026),
    ("maestro", "670301", "Banque du Rhone", "BE", "debit", 0.012),
    ("visa", "400010", "Prepay Solutions", "IE", "prepaid", 0.023),
    ("mastercard", "519999", "Cardo Prepaid", "MT", "prepaid", 0.018),
    ("visa", "455701", "Fintech Neo", "LT", "prepaid", 0.015),
    ("mastercard", "525803", "Neobanco Uno", "ES", "debit", 0.034),
    ("visa", "419902", "Neobanco Uno", "ES", "credit", 0.017),
    ("discover", "601100", "Sunbelt Financial", "US", "credit", 0.007),
    ("jcb", "353011", "Kanto Card", "JP", "credit", 0.005),
    ("unionpay", "622202", "Pacific Union", "SG", "debit", 0.004),
    ("visa", "491700", "Banco Azteca Sur", "MX", "debit", 0.006),
    ("mastercard", "548031", "Banco do Sul", "BR", "credit", 0.005),
]

# (code, ISO 8583 network code, description, category, is_soft, retry_success_lift)
# Soft declines are worth retrying; hard ones are not, and a system that retries a
# stolen-card decline is a system that gets fined.
DECLINE_REASONS: list[tuple] = [
    ("insufficient_funds", "51", "Insufficient funds", "SOFT_FUNDS", True, 0.34),
    ("do_not_honor", "05", "Do not honour", "SOFT_ISSUER", True, 0.28),
    ("issuer_unavailable", "91", "Issuer or switch inoperative", "SOFT_TECHNICAL", True, 0.71),
    ("transaction_timeout", "68", "Response received too late", "SOFT_TECHNICAL", True, 0.66),
    ("sca_required", "65", "Strong authentication required", "SOFT_AUTH", True, 0.62),
    ("incorrect_cvv", "82", "Incorrect CVV", "SOFT_INPUT", True, 0.41),
    ("expired_card", "54", "Expired card", "HARD_CARD", False, 0.04),
    ("invalid_card_number", "14", "Invalid card number", "HARD_CARD", False, 0.03),
    ("lost_card", "41", "Lost card, pick up", "HARD_FRAUD", False, 0.01),
    ("stolen_card", "43", "Stolen card, pick up", "HARD_FRAUD", False, 0.01),
    ("restricted_card", "62", "Restricted card", "HARD_CARD", False, 0.05),
    ("suspected_fraud", "59", "Suspected fraud", "HARD_FRAUD", False, 0.02),
    ("exceeds_limit", "61", "Exceeds withdrawal limit", "SOFT_LIMIT", True, 0.22),
    ("velocity_exceeded", "65", "Activity count exceeded", "SOFT_LIMIT", True, 0.19),
    ("invalid_merchant", "03", "Invalid merchant", "HARD_CONFIG", False, 0.02),
    ("currency_not_supported", "12", "Invalid transaction", "HARD_CONFIG", False, 0.03),
    ("blocked_by_risk_engine", "-", "Blocked by CIERZO risk engine", "BLOCKED", False, 0.06),
    ("three_ds_failed", "-", "3-D Secure authentication failed", "SOFT_AUTH", True, 0.38),
]

# (os family, [version pool], device class, [model pool])
DEVICE_MODELS: list[tuple] = [
    (
        "iOS",
        ["17.4", "17.6", "18.1", "18.3", "19.0"],
        "mobile",
        ["iPhone 14", "iPhone 15", "iPhone 15 Pro", "iPhone 16", "iPhone 16 Pro", "iPhone SE"],
    ),
    ("iPadOS", ["17.5", "18.2", "19.0"], "tablet", ["iPad Air", "iPad Pro 11", "iPad mini"]),
    (
        "Android",
        ["13", "14", "15", "16"],
        "mobile",
        [
            "Galaxy S23",
            "Galaxy S24",
            "Galaxy A54",
            "Pixel 8",
            "Pixel 9",
            "Xiaomi 14",
            "Redmi Note 13",
            "OnePlus 12",
        ],
    ),
    ("Android", ["13", "14", "15"], "tablet", ["Galaxy Tab S9", "Redmi Pad"]),
    ("Windows", ["10", "11"], "desktop", ["Windows PC"]),
    (
        "macOS",
        ["14.5", "15.1", "15.4", "26.0"],
        "desktop",
        ["MacBook Air", "MacBook Pro", "iMac", "Mac mini"],
    ),
    ("Linux", ["6.8", "6.11"], "desktop", ["Linux Desktop"]),
]
BROWSERS = [
    ("Chrome", 0.446),
    ("Safari", 0.281),
    ("Edge", 0.108),
    ("Firefox", 0.074),
    ("Samsung Internet", 0.051),
    ("Opera", 0.023),
    ("Brave", 0.017),
]

# (ASN, ISP name, kind). `kind` is what a risk engine actually cares about:
# a payment from a datacentre range at 04:00 is not the same as one from a
# residential line, and that distinction has to exist in the data for the
# risk score to mean anything.
ASN_POOL: list[tuple] = [
    # INVENTED operators with plausible names and ASNs from the private-use range
    # (64512-65534 and 4200000000+). The first version used the real numbers and
    # names -- Telefonica, Deutsche Telekom, and then Google Cloud, Amazon AWS and
    # Microsoft Azure carrying `is_anonymizer = TRUE` and a made-up risk weight.
    # Publishing a real company's name next to an invented fraud label is a
    # different kind of problem from publishing synthetic data, and the same
    # discipline that kept every IP inside 10.0.0.0/8 has to apply here.
    (64512, "Telcoiberia Fibra", "residential"),
    (64513, "Nubia Movil ES", "residential"),
    (64514, "Redes del Sur", "residential"),
    (64515, "CableAtlantico", "residential"),
    (64516, "Lusonet", "residential"),
    (64517, "Atlantica Telecom", "residential"),
    (64518, "Reseau Hexagone", "residential"),
    (64519, "Libre Telecom FR", "residential"),
    (64520, "Rheinnetz", "residential"),
    (64521, "Bundesfaser", "residential"),
    (64522, "Adriatica Net", "residential"),
    (64523, "Ventiquattro Telecom", "residential"),
    (64524, "Albion Broadband", "residential"),
    (64525, "Thames Fibre", "residential"),
    (64526, "Lagelijn", "residential"),
    (64527, "Benelux Net", "residential"),
    (64528, "Wisla Telekom", "residential"),
    (64529, "Nordljus AB", "residential"),
    (64530, "Sundnet", "residential"),
    (64531, "Fjordlink", "residential"),
    (65001, "Cirrus Hosting", "datacenter"),
    (65002, "Northstack Cloud", "datacenter"),
    (65003, "Blueforge Compute", "datacenter"),
    (65004, "Kernel Droplets", "datacenter"),
    (65005, "Bauhaus Servers", "datacenter"),
    (65006, "Aquila Hosting", "datacenter"),
    (65007, "Vectra Cloud", "datacenter"),
    (65008, "Contado Systems", "datacenter"),
    (65101, "Veilnet Privacy", "vpn"),
    (65102, "Tunnelworks", "vpn"),
    (65103, "Cloakline VPN", "vpn"),
    (65104, "Shroud Networks", "vpn"),
    (65201, "Movilia Wireless", "mobile"),
    (65202, "Airwave Mobile", "mobile"),
    (65203, "Celeris Mobile UK", "mobile"),
    (65204, "Rapida Mobile IT", "mobile"),
]

# Building blocks for the corporate ownership graph. A holding named after a river
# owning an operating company named after a mountain is not a joke: it is exactly
# how European group structures read, and it makes the recursive query legible.
GROUP_PREFIX = [
    "Grupo",
    "Holding",
    "Corporacion",
    "Compania",
    "Sociedad",
    "Inversiones",
    "Participaciones",
    "Capital",
    "Grupo Industrial",
]
GROUP_ROOT = [
    "Aranjuez",
    "Bidasoa",
    "Cinca",
    "Duero",
    "Ebro",
    "Fluvia",
    "Genil",
    "Huerva",
    "Iregua",
    "Jalon",
    "Kadagua",
    "Llobregat",
    "Mijares",
    "Najerilla",
    "Odiel",
    "Pisuerga",
    "Queiles",
    "Riaza",
    "Segura",
    "Tormes",
    "Ulla",
    "Verde",
    "Xuquer",
    "Yeltes",
    "Zadorra",
    "Aragon",
    "Bernesga",
    "Carrion",
    "Deva",
    "Esla",
    "Flumen",
    "Gallego",
    "Henares",
    "Isuela",
    "Jarama",
    "Kamino",
]
GROUP_SUFFIX = [
    "SA",
    "SL",
    "SAU",
    "Holdings BV",
    "Group PLC",
    "GmbH",
    "SARL",
    "SpA",
    "AB",
    "AS",
    "NV",
    "Limited",
    "Holdings SA",
]

MERCHANT_PREFIX = [
    "Casa",
    "Tienda",
    "Almacen",
    "Boutique",
    "Mercado",
    "Punto",
    "Espacio",
    "Estudio",
    "Taller",
    "Bazar",
    "Central",
    "Norte",
    "Sur",
    "Nova",
    "Bella",
    "Buena",
    "Gran",
    "Nueva",
    "Real",
]
MERCHANT_ROOT = [
    "Aurora",
    "Bahia",
    "Calma",
    "Dorada",
    "Encina",
    "Fuente",
    "Gaviota",
    "Hoja",
    "Islas",
    "Jara",
    "Ladera",
    "Marea",
    "Nube",
    "Olivo",
    "Puerto",
    "Quilla",
    "Retama",
    "Sierra",
    "Tejo",
    "Umbria",
    "Vela",
    "Xara",
    "Yedra",
    "Zarza",
    "Almendro",
    "Brisa",
    "Cumbre",
    "Dehesa",
    "Estrella",
    "Faro",
    "Granada",
    "Hiedra",
    "Invierno",
    "Junco",
    "Kiwi",
    "Laurel",
    "Manzano",
    "Naranjo",
]
SITE_KINDS = [
    "web",
    "app_ios",
    "app_android",
    "marketplace",
    "pos_terminal",
    "call_center",
    "kiosk",
    "subscription_billing",
]

# The employee hierarchy is self-referencing, which is the other recursive query
# in the catalogue and the one that reaches personal data.
JOB_TITLES = [
    ("Chief Executive Officer", 0),
    ("Chief Financial Officer", 1),
    ("Chief Risk Officer", 1),
    ("VP Operations", 2),
    ("VP Sales", 2),
    ("Head of Payment Operations", 3),
    ("Head of Risk", 3),
    ("Head of Account Management", 3),
    ("Finance Manager", 4),
    ("Account Manager", 5),
    ("Payment Operations Agent", 5),
    ("Risk Analyst", 5),
    ("Data Analyst", 5),
    ("Settlement Analyst", 5),
    ("Dispute Specialist", 5),
    ("Onboarding Specialist", 5),
]


def name_pools(seed: int) -> dict[str, np.ndarray]:
    """Draw person-name and address components ONCE, per locale.

    Faker is fast enough for a few thousand calls and hopelessly slow for tens of
    millions. Pools plus integer indices give the same variety at a thousand times
    the speed, and are what makes a nine-million-row customer dimension take
    seconds instead of an afternoon.
    """
    locales = {
        "ES": "es_ES",
        "FR": "fr_FR",
        "DE": "de_DE",
        "IT": "it_IT",
        "PT": "pt_PT",
        "GB": "en_GB",
        "NL": "nl_NL",
        "BE": "nl_BE",
        "IE": "en_IE",
        "PL": "pl_PL",
        "SE": "sv_SE",
        "DK": "da_DK",
        "NO": "no_NO",
        "FI": "fi_FI",
        "AT": "de_AT",
        "CH": "de_CH",
        "CZ": "cs_CZ",
        "HU": "hu_HU",
        "RO": "ro_RO",
        "BG": "bg_BG",
        "GR": "el_GR",
        "US": "en_US",
        "MX": "es_MX",
        "BR": "pt_BR",
        "MA": "fr_FR",
        "TR": "tr_TR",
        "SG": "en_US",
        "JP": "ja_JP",
        "LT": "lt_LT",
        "MT": "en_GB",
    }
    out: dict[str, np.ndarray] = {}
    for cc, loc in locales.items():
        fake = Faker(loc)
        fake.seed_instance(seed + abs(hash(cc)) % 100_000)
        firsts, lasts, streets = set(), set(), set()
        for _ in range(1400):
            firsts.add(fake.first_name())
            lasts.add(fake.last_name())
        for _ in range(700):
            streets.add(fake.street_name())
        out[f"{cc}_first"] = np.array(sorted(firsts), dtype=object)
        out[f"{cc}_last"] = np.array(sorted(lasts), dtype=object)
        out[f"{cc}_street"] = np.array(sorted(streets), dtype=object)
    return out


EMAIL_DOMAINS = [
    ("gmail.com", 0.331),
    ("hotmail.com", 0.121),
    ("outlook.com", 0.094),
    ("yahoo.com", 0.061),
    ("icloud.com", 0.078),
    ("proton.me", 0.031),
    ("gmx.de", 0.024),
    ("web.de", 0.021),
    ("orange.fr", 0.019),
    ("free.fr", 0.014),
    ("telefonica.net", 0.017),
    ("terra.es", 0.009),
    ("libero.it", 0.016),
    ("sapo.pt", 0.011),
    ("wp.pl", 0.013),
    ("btinternet.com", 0.012),
    ("live.com", 0.028),
    ("me.com", 0.014),
    ("yandex.com", 0.006),
    ("zoho.com", 0.005),
    ("fastmail.com", 0.004),
    ("mail.ru", 0.003),
    ("aol.com", 0.007),
    ("tutanota.com", 0.003),
    ("empresa-ejemplo.es", 0.058),  # corporate mail: a segment, not noise
]
