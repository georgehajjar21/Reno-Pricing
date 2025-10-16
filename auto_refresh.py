import json, datetime, os

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "prices.json")

def refresh_prices():
    with open(DATA_PATH, "r+", encoding="utf-8") as f:
        data = json.load(f)
        data["last_refreshed"] = datetime.datetime.now().strftime("%Y-%m-%d")
        for k, v in data["base_rates"].items():
            for subkey in v:
                if isinstance(v[subkey], (int, float)):
                    v[subkey] = round(v[subkey] * 1.015, 2)
        f.seek(0)
        json.dump(data, f, indent=2)
        f.truncate()

if __name__ == "__main__":
    refresh_prices()
