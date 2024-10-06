# Search.Libre: An OpenNIC Search Engine

This search engine is available at [search.libre:45260](http://search.libre:45260).

## Getting Started

If you would like to host your own version of this site, for development purposes or otherwise, follow these steps:

### 1. Set up a Virtual Environment

- Create a Python virtual environment inside the empty `.venv` folder.
- This has been tested with **Python 3.10**.

### 2. Build the OpenNIC Search Package

- While in the `opennic_search` folder, run:

  ```bash
  maturin develop
  ```

  _Note: You can safely ignore the warning about the `extension-module` feature not being enabled._

### 3. Compile the Project

- Build the project using Cargo:

  ```bash
  cargo build -r
  ```

### 4. Host the Website

- After building, run the following command to host the site:

  ```bash
  cargo run -r
  ```

### 5. Start the Crawler

- Start the Python crawler (with the venv activated!):

  ```bash
  python main.py
  ```

  _Important: The search engine relies on the crawler for finding sites, so ensure the crawler is running unless you want no search results._

## Configuration

The following options can be adjusted in the `config.json` file:

- **`hostname`**: The domain the site accepts requests from (e.g., `search.libre`).
- **`port`**: The port number the site will use. Defaults to `80` for HTTP.
- **`bind_address`**: The IP address the server will listen on. Set it to `127.0.0.1` to bind to localhost or `0.0.0.0` for external access.
- **`restrict_hostname`**: If `true`, the server only accepts requests from the specified `hostname`. Otherwise, it accepts requests from any hostname.
