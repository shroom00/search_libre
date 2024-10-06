use std::{cmp::min, collections::HashMap, fs::read_to_string, net::Ipv4Addr};

use actix_files::Files;
use actix_web::{
    body::{BoxBody, EitherBody, MessageBody},
    dev::{ServiceRequest, ServiceResponse},
    get,
    middleware::{from_fn, Condition, Next},
    post,
    web::{self, block},
    App, HttpRequest, HttpResponse, HttpServer, Responder,
};
use once_cell::sync::Lazy;
use pyo3::{
    prepare_freethreaded_python,
    types::{PyAnyMethods, PyDict, PyList, PyModule},
    Python,
};
use rusqlite::Result;
use serde::{Deserialize, Serialize};
use tera::{Context, Tera};

use crate::no_context_route;

#[derive(Debug, Serialize)]
pub struct SearchResult {
    pub url: String,
    pub title: String,
    pub snippet: String,
}

#[derive(Debug, Serialize)]
pub struct SearchResults {
    pub results: Vec<SearchResult>,
    pub duration: f32,
    pub total: u32,
    pub exact: bool,
    pub is_last: bool,
    pub pagenum: u32,
    pub maxpage: u32,
    pub query: String,
    pub valid: bool,
}

#[derive(Deserialize, Debug)]
struct SearchQuery {
    q: Option<String>,
    p: Option<u32>,
}

pub fn init_py() {
    prepare_freethreaded_python();
    Python::with_gil(|py| {
        // Disables Python's sigint handler, which usually catches (and then ignores) ctrl+c, causing web server to be un-interruptable
        // Thank you to: https://github.com/PyO3/pyo3/issues/3218
        py.run_bound(
            r#"
import signal
signal.signal(signal.SIGINT, signal.SIG_DFL)
          "#,
            None,
            None,
        )
        .unwrap();
        // Import the `sys` module so we can manipulate `sys.path`
        let sys = PyModule::import_bound(py, "sys").unwrap();
        let path = sys.getattr("path").unwrap();
        path.call_method1("append", ("../crawler/",)).unwrap();
        path.call_method1("append", ("../.venv/Lib/site-packages/",))
            .unwrap();
        // search(String::from("init"), 1);
        // the first search is noticeably slower, so we do a search here to make sure the function is loaded or something
        // it works, idk
    })
}

pub async fn search(query: String, pagenum: u32) -> SearchResults {
    let results = block(move || {
        Python::with_gil(|py| unsafe {
            let results_dict = PyModule::import_bound(py, "whoosh_backend")
                .unwrap()
                .call_method1("search", (&query, "../records", pagenum))
                .unwrap()
                .downcast_into_unchecked::<PyDict>();

            if !results_dict
                .get_item("valid")
                .unwrap()
                .extract::<bool>()
                .unwrap()
            {
                return SearchResults {
                    results: vec![],
                    duration: 0.0,
                    total: 0,
                    exact: true,
                    is_last: true,
                    pagenum: 0,
                    maxpage: 0,
                    query,
                    valid: false,
                };
            }
            let results: Vec<SearchResult> = results_dict
                .get_item("results")
                .unwrap()
                .downcast_into_unchecked::<PyList>()
                .into_iter()
                .map(|r| SearchResult {
                    url: r.get_item("url").unwrap_unchecked().to_string(),
                    title: r.get_item("title").unwrap_unchecked().to_string(),
                    snippet: r.get_item("snippet").unwrap_unchecked().to_string(),
                })
                .collect();
            let duration = results_dict
                .get_item("duration")
                .unwrap_unchecked()
                .extract::<f32>()
                .unwrap_unchecked();
            let total = results_dict
                .get_item("total")
                .unwrap_unchecked()
                .extract::<u32>()
                .unwrap_unchecked();
            let exact = results_dict
                .get_item("exact")
                .unwrap_unchecked()
                .extract::<bool>()
                .unwrap_unchecked();
            let is_last = results_dict
                .get_item("last")
                .unwrap_unchecked()
                .extract::<bool>()
                .unwrap_unchecked();
            let maxpage = results_dict
                .get_item("maxpage")
                .unwrap_unchecked()
                .extract::<u32>()
                .unwrap_unchecked();

            SearchResults {
                results,
                duration,
                total,
                exact,
                is_last,
                pagenum: min(maxpage, pagenum),
                maxpage,
                query,
                valid: true,
            }
        })
    })
    .await;
    results.unwrap()
}

#[get("/search")]
async fn search_route(params: web::Query<SearchQuery>, tera: web::Data<Tera>) -> impl Responder {
    match &params.q {
        Some(query) => {
            let pagenum = params.p.unwrap_or(1);
            let search_results = search(query.to_owned(), pagenum).await;

            let context = Context::from_serialize(search_results).unwrap();

            // Render the template using Tera
            match tera.render("results.tera", &context) {
                Ok(rendered) => HttpResponse::Ok().content_type("text/html").body(rendered),
                Err(_) => HttpResponse::InternalServerError().body("Error rendering template"),
            }
        }
        None => HttpResponse::BadRequest().finish(),
    }
}

no_context_route!("/", index, "index.tera");
no_context_route!("/faq", faq, "faq.tera");
no_context_route!("/add_url", get_add_url, "add_url.tera");

// Route handler to add a URL
#[post("/add_url")]
async fn add_url(req: HttpRequest, form: web::Form<UrlFormData>) -> impl Responder {
    let info = req.connection_info();
    let ip_str = info.realip_remote_addr().unwrap();
    let ip_u32 = u32::from(ip_str.parse::<Ipv4Addr>().unwrap());
    match crate::add_url(
        "../urls.db".to_string(),
        form.url.clone(),
        ip_u32,
        Some(3600),
    ) {
        Ok(added_url) => HttpResponse::Ok().json({
            let mut j = HashMap::new();
            j.insert("url", added_url);
            j
        }),
        Err(e) => match e {
            crate::PyErrEnum::RateLimitError(wait) => {
                let msg = format!("You have been rate limited. Please wait {wait} second(s).");
                HttpResponse::TooManyRequests()
                    .insert_header(("Retry-After", wait))
                    .json({
                        let mut j = HashMap::new();
                        j.insert("error", msg);
                        j
                    })
            }
            crate::PyErrEnum::SqlError(error) => {
                let msg = format!("There was an SQL backend error: {error}");
                HttpResponse::InternalServerError().json({
                    let mut j = HashMap::new();
                    j.insert("error", msg);
                    j
                })
            }
            crate::PyErrEnum::UrlParseError(parse_error) => {
                let msg = format!("There was an error parsing the URL: {parse_error}");
                HttpResponse::BadRequest().json({
                    let mut j = HashMap::new();
                    j.insert("error", msg);
                    j
                })
            }
        },
    }
}

// Route handler to get all URLs
#[get("/get_urls")]
async fn get_urls() -> impl Responder {
    match crate::get_urls("../urls.db".to_string()) {
        Ok(urls) => HttpResponse::Ok().json(urls), // Return as JSON
        Err(_) => HttpResponse::ImATeapot().finish(),
    }
}

// Constructed Responses always seem to end up with body of type BoxBody
// Unsure if this is intended or a fault in my code
// Using EitherBody fixes this, but may not be the preferred way to handle it
/// This Middleware ensures the Host header matches the host in the config file, otherwise a 404 is returned
async fn host_middleware(
    req: ServiceRequest,
    next: Next<impl MessageBody>,
) -> Result<ServiceResponse<EitherBody<impl MessageBody, BoxBody>>, actix_web::Error> {
    let not_found = |req: ServiceRequest| {
        Ok(req.into_response(HttpResponse::NotFound().finish().map_into_right_body()))
    };
    let host = match req.headers().get("Host") {
        Some(host) => {
            let host = host.to_str().unwrap();
            host.split(':').next().unwrap_or(host)
        }
        None => return not_found(req),
    };

    match host.starts_with(&CONFIG.hostname) {
        true => Ok(next.call(req).await?.map_into_left_body()),
        false => not_found(req),
    }
}

// Struct to receive form data
#[derive(Deserialize)]
struct UrlFormData {
    url: String,
}

#[derive(Debug, Deserialize)]
struct Config {
    hostname: String,
    port: u16,
    bind_address: String,
    restrict_hostname: bool,
}

fn load_config() -> Config {
    let file_content = read_to_string("config.json").expect("Failed to read config file");
    serde_json::from_str::<Config>(&file_content)
        .expect("Failed to construct config from config file.")
}

static CONFIG: Lazy<Config> = Lazy::new(|| load_config());

#[actix_web::main]
pub async fn main() -> std::io::Result<()> {
    init_py();
    let tera = Tera::new("./templates/*").unwrap();
    let server = match HttpServer::new(move || {
        {
            App::new()
                .wrap(Condition::new(
                    CONFIG.restrict_hostname,
                    from_fn(host_middleware),
                ))
                .app_data(web::Data::new(tera.clone())) // Share Tera instance across handlers
                .service(Files::new("/js", "./js"))
                .service(Files::new("/css", "./css"))
                .service(Files::new("/img", "./img"))
                .service(index)
                .service(search_route)
                .service(faq)
                .service(add_url)
                .service(get_add_url)
                .service(get_urls)
                .service(Files::new("/", "./static"))
        }
    })
    .bind((CONFIG.bind_address.as_str(), CONFIG.port))
    {
        Ok(server) => server,
        Err(e) => return Result::Err(e),
    };

    let addrs = server.addrs_with_scheme();
    println!("Site running at:");
    addrs
        .into_iter()
        .for_each(|(addr, scheme)| println!("\t{scheme}://{addr}"));

    let server = server.run();
    server.await
}
