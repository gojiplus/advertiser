import openai
import sys
import os

def summarize(repo_url, description):
    client = openai.OpenAI()  # Initialize the client
    prompt = f"Write a crisp 1-line summary of this repo:\nURL: {repo_url}\nDescription: {description}"
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()

if __name__ == "__main__":
    url = sys.argv[1]
    desc = sys.argv[2]
    print(summarize(url, desc))