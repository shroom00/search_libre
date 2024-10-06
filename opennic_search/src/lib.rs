pub mod web;
pub mod utils;

use std::error::Error;
use std::fmt::{self, Debug, Display};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::{exceptions::PyValueError, types::PyString};
use rusqlite::{params, Connection, Error as rusqliteError, OptionalExtension};
use url::{ParseError, Url};

#[pyclass]
#[pyo3(name = "RateLimitExceeded", extends = PyException)]
#[derive(Debug)]
pub struct RateLimitExceeded {
    #[pyo3(get)]
    pub wait_time: u64,
}

#[pymethods]
impl RateLimitExceeded {
    #[new]
    #[pyo3(signature = (_msg, wait_time))]
    fn new(_msg: String, wait_time: u64) -> Self {
        Self { wait_time }
    }

    fn __str__(&self) -> String {
        format!(
            "You have been rate limited. Please wait {} second(s).",
            self.wait_time
        )
    }
}

impl Display for RateLimitExceeded {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.__str__())
    }
}

impl From<RateLimitExceeded> for PyErr {
    fn from(value: RateLimitExceeded) -> Self {
        PyErr::new::<RateLimitExceeded, _>((value.to_string(), value.wait_time))
    }
}

#[derive(Debug)]
enum PyErrEnum {
    RateLimitError(u64),
    SqlError(rusqliteError),
    UrlParseError(ParseError),
}

fn add_url<'a>(
    fp: String,
    url: String,
    ip: u32,
    rate_limit_seconds: Option<u64>,
) -> Result<String, PyErrEnum> {
    let rate_limit_seconds = rate_limit_seconds.unwrap_or(0);

    let parsed_url = Url::parse(&url).map_err(|e| PyErrEnum::UrlParseError(e))?;
    let base_url = parsed_url.origin().unicode_serialization();

    let start = SystemTime::now();
    let timestamp = start.duration_since(UNIX_EPOCH).unwrap().as_secs();

    if !Path::new(&fp).exists() {
        create_db(&fp).unwrap();
    }
    let open = Connection::open(fp);
    let conn = open.map_err(|e| PyErrEnum::SqlError(e))?;

    let mut stmt = conn
        .prepare(
            "SELECT timestamp FROM urls
         WHERE ip = ?1
         ORDER BY timestamp DESC
         LIMIT 1",
        )
        .map_err(|e| PyErrEnum::SqlError(e))?;

    let last_timestamp: Option<u64> = stmt
        .query_row(params![ip], |row| row.get(0))
        .optional()
        .map_err(|e| PyErrEnum::SqlError(e))?;

    if let Some(last_timestamp) = last_timestamp {
        let time_diff = timestamp - last_timestamp;
        if time_diff < rate_limit_seconds {
            return Err(PyErrEnum::RateLimitError(rate_limit_seconds - time_diff));
        }
    }

    conn.execute(
        "INSERT INTO urls (url, ip, timestamp) VALUES (?1, ?2, ?3)",
        params![base_url, ip, timestamp],
    )
    .map_err(|e| PyErrEnum::SqlError(e))?;

    Ok(base_url)
}

#[allow(dead_code)]
#[pyfunction(name = "add_url")]
#[pyo3(text_signature = "(fp: str, url: str, ip: int, rate_limit_seconds: Option[int] = None)", signature=(fp, url, ip, rate_limit_seconds=None))]
fn add_url_py<'a>(
    fp: &'a Bound<'_, PyString>,
    url: &'a Bound<'_, PyString>,
    ip: u32,
    rate_limit_seconds: Option<u64>,
) -> PyResult<()> {
    match add_url(fp.to_string(), url.to_string(), ip, rate_limit_seconds) {
        Ok(_) => Ok(()),
        Err(e) => match e {
            PyErrEnum::RateLimitError(wait_time) => Err(RateLimitExceeded { wait_time }.into()),
            PyErrEnum::SqlError(e) => Err(PyErr::new::<PyValueError, _>(e.to_string())),
            PyErrEnum::UrlParseError(e) => Err(PyErr::new::<PyValueError, _>(e.to_string())),
        },
    }
}

pub fn get_urls(fp: String) -> Result<Vec<String>, Box<dyn Error>> {
    if !Path::new(&fp).exists() {
        create_db(&fp).unwrap();
    }
    let mut conn = Connection::open(fp)?;

    let tx = conn.transaction()?;

    let mut statement = tx.prepare("SELECT url FROM urls").unwrap();
    let url_iter = statement.query_map([], |row| row.get(0))?;

    let mut urls = Vec::new();
    for url in url_iter {
        let url: String = url?;
        urls.push(url.clone());
    }
    statement.finalize()?;

    tx.execute("DELETE FROM urls", [])?;

    tx.commit()?;

    Ok(urls)
}

pub fn create_db(fp: &str) -> Result<(), Box<dyn Error>> {
    let conn = Connection::open(fp)?;

    conn.execute(
        "CREATE TABLE IF NOT EXISTS urls (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            ip INTEGER NOT NULL,
            timestamp INTEGER DEFAULT (strftime('%s', 'now'))
        )",
        [],
    )?;
    Ok(())
}

#[pyfunction(name = "create_db")]
#[pyo3(text_signature = "(fp: str)")]
fn create_db_py<'a>(fp: &'a Bound<'_, PyString>) -> PyResult<()> {
    create_db(&fp.to_string()).map_err(|e| PyErr::new::<PyValueError, _>(e.to_string()))
}
/// Python interface to retrieve and remove all URLs from the database.
#[pyfunction(name = "get_urls")]
#[pyo3(text_signature = "(fp: str)")]
fn get_urls_py<'a>(fp: &'a Bound<'_, PyString>) -> PyResult<Vec<String>> {
    get_urls(fp.to_string()).map_err(|e| PyErr::new::<PyValueError, _>(e.to_string()))
}

/// A Python module implemented in Rust.
#[pymodule]
#[pyo3(name = "opennic_search")]
fn opennic_search(py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(create_db_py, m)?)?;
    m.add_function(wrap_pyfunction!(add_url_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_urls_py, m)?)?;
    m.add(
        "RateLimitExceeded",
        py.get_type_bound::<RateLimitExceeded>(),
    )?;
    Ok(())
}
