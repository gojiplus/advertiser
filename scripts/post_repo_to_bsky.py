# Create post text
post_text = f"📦 {title}\n{summary}\n{url}"

# Find character‐based indices
char_start = post_text.find(url)
if char_start < 0:
    raise ValueError("URL not found in post_text")

# Compute byte‐based indices
prefix = post_text[:char_start]
byte_start = len(prefix.encode('utf-8'))
byte_end   = byte_start + len(url.encode('utf-8'))

# Create the facet
facets = [
    {
        "index": {"byteStart": byte_start, "byteEnd": byte_end},
        "features": [
            {
                "$type": "app.bsky.richtext.facet#link",
                "uri": url
            }
        ]
    }
]

