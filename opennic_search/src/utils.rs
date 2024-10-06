/// A macro to define a GET route for serving a Tera template without context.
///
/// # Arguments
/// - `$endpoint`: The route path (e.g., `"/about"`).
/// - `$func`: The handler function name.
/// - `$filepath`: The Tera template file (e.g., `"about.html"`).
///
/// # Example
/// ```rust
/// no_context_route!("/about", about_handler, "about.html");
/// ```
/// Generates a route for `/about` that renders `about.html` without context.
#[macro_export]
macro_rules! no_context_route {
    ($endpoint:literal, $func:ident, $filepath:literal) => {
        #[get($endpoint)]
        async fn $func(tera: web::Data<Tera>) -> impl Responder {
            match tera.render($filepath, &Context::new()) {
                Ok(rendered) => HttpResponse::Ok().content_type("text/html").body(rendered),
                Err(_) => HttpResponse::InternalServerError().body("Error rendering template"),
            }
        }
    };
}
