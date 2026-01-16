import requests
import time
import os
import shutil

API_URL = "http://localhost:8000"
TEST_FOLDER = os.path.abspath("test_watch_folder")
TEST_FILE = os.path.join(TEST_FOLDER, "test_doc.txt")


def setup():
    if not os.path.exists(TEST_FOLDER):
        os.makedirs(TEST_FOLDER)

    # ensure it's watched
    requests.post(f"{API_URL}/files/watched", json={"path": TEST_FOLDER})
    time.sleep(1)  # wait for sync


def main():
    setup()

    print(f"Creating file {TEST_FILE}...")
    with open(TEST_FILE, "w") as f:
        f.write("Version 1 content")

    time.sleep(1)  # Wait for monitor to pick up created event

    print("Modifying file to Version 2...")
    with open(TEST_FILE, "w") as f:
        f.write("Version 2 content (modified)")

    time.sleep(1)

    # Check versions
    print("Checking versions API...")
    resp = requests.get(f"{API_URL}/files/versions", params={"path": TEST_FILE})
    versions = resp.json()
    print("Versions found:", len(versions))
    for v in versions:
        print(f" - V{v['version_number']}: Size={v['file_size_bytes']}")

    if len(versions) < 2:
        print("FAIL: Expected at least 2 versions")
        return

    # Restore Version 1
    v1 = versions[-1]  # Oldest
    print(f"Restoring V{v1['version_number']}...")
    restore_resp = requests.post(
        f"{API_URL}/files/restore", json={"version_id": v1["id"]}
    )
    print("Restore response:", restore_resp.json())

    # Verify content
    with open(TEST_FILE, "r") as f:
        content = f.read()
        print("Current content:", content)
        if content == "Version 1 content":
            print("SUCCESS: File restored to Version 1")
        else:
            print("FAIL: Content mismatch")


if __name__ == "__main__":
    main()
